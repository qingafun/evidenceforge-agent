from types import SimpleNamespace

import httpx
import pytest

from evidenceforge.tools import ToolError, ToolRegistry


CHUNK = {"id": "doc-1:0", "text": "SQLite persists local state.", "title": "Persistence", "source": "local"}


class Knowledge:
    def search(self, query, limit=6):
        return [CHUNK][:limit] if "SQLite" in query else []

    def read_chunk(self, chunk_id):
        return CHUNK if chunk_id == CHUNK["id"] else None


class Store:
    def list_memories(self):
        return [{"id": "mem-1", "content": "Prefer local SQLite", "created_at": "2026-09-21"},
                {"id": "mem-2", "content": "Budget is limited", "created_at": "2026-09-21"}]


@pytest.fixture
def registry():
    return ToolRegistry(Knowledge(), Store(), SimpleNamespace(tavily_api_key="", request_timeout=5))


def test_local_search_read_and_memory(registry):
    assert registry.execute("search_knowledge", {"query": "SQLite"}) == {"results": [CHUNK]}
    assert registry.execute("read_source", {"id": CHUNK["id"]}) == {"result": CHUNK}
    assert registry.execute("read_source", {"id": "missing"}) == {"result": None}
    assert registry.execute("search_memory", {"query": "sqlite"})["results"][0]["id"] == "mem-1"
    assert registry.execute("search_memory", {"query": "unknown"}) == {"results": []}


@pytest.mark.parametrize("name,args", [
    ("shell", {"command": "ls"}), ("search_web", {"query": "a"}),
    ("calculator", {"expression": "2+2", "extra": True}),
    ("search_knowledge", {"query": "a", "limit": "2"}),
    ("search_knowledge", {"query": "a", "limit": True}),
    ("search_knowledge", {"query": "a", "limit": 11}),
    ("search_knowledge", {"query": "a" * 1001}),
    ("search_knowledge", {"query": "   "}),
    ("search_knowledge", ["not", "an object"]),
    ("read_source", {"id": "../../secret"}),
])
def test_tool_argument_validation(registry, name, args):
    with pytest.raises(ToolError):
        registry.execute(name, args)


@pytest.mark.parametrize("expression, expected", [
    ("2 + 3 * 4", 14), ("(20 - 5) / 3", 5), ("-4 + 7 % 3", -3),
    ("2 ** 10", 1024), ("2 ** -2", 0.25), ("1.5 * 4", 6),
])
def test_calculator_arithmetic(registry, expression, expected):
    assert registry.execute("calculator", {"expression": expression}) == {"result": expected}


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo bad')", "(1).__class__", "[x for x in [1]]", "True + 1",
    "1 / 0", "1 % 0", "10 ** 999999999", "10 ** 10 ** 10", "1e309", "1e12 * 2",
    "(-1) ** 0.5", "1 << 4", "max(1, 2)", "2 // 1", "'hello'", "+" * 50 + "1",
])
def test_calculator_rejects_unsafe_or_unbounded_expressions(registry, expression):
    with pytest.raises(ToolError):
        registry.execute("calculator", {"expression": expression})


def test_schemas_are_native_strict_and_web_is_opt_in(registry):
    schemas = registry.schemas()
    assert {s["function"]["name"] for s in schemas} == {
        "search_knowledge", "read_source", "calculator", "search_memory",
    }
    assert all(s["type"] == "function" for s in schemas)
    assert all(s["function"]["parameters"]["additionalProperties"] is False for s in schemas)


def test_tavily_normalizes_bounds_and_keeps_sources_readable(monkeypatch):
    actual_client = httpx.Client
    seen = []

    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://api.tavily.com/search"
        return httpx.Response(200, json={"results": [
            {"url": "https://example.org/reference", "title": "A reference", "content": "x" * 7000, "score": 0.9},
            {"url": "file:///secret", "title": "Bad", "content": "bad"},
        ]})

    monkeypatch.setattr("evidenceforge.tools.httpx.Client", lambda **kwargs: actual_client(transport=httpx.MockTransport(handler)))
    registry = ToolRegistry(Knowledge(), Store(), SimpleNamespace(tavily_api_key="test", request_timeout=5))
    assert "search_web" in {s["function"]["name"] for s in registry.schemas()}
    results = registry.execute("search_web", {"query": "persistence"})["results"]
    assert len(results) == 1
    assert results[0]["id"].startswith("web-")
    assert len(results[0]["text"]) == 6000
    assert registry.execute("read_source", {"id": results[0]["id"]})["result"] == results[0]
    assert len(seen) == 1


def test_web_failure_does_not_expose_provider_body(monkeypatch):
    actual_client = httpx.Client
    monkeypatch.setattr("evidenceforge.tools.httpx.Client", lambda **kwargs: actual_client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="SECRET_API_KEY"))))
    registry = ToolRegistry(Knowledge(), Store(), SimpleNamespace(tavily_api_key="test", request_timeout=5))
    with pytest.raises(ToolError) as error:
        registry.execute("search_web", {"query": "anything"})
    assert "SECRET" not in str(error.value)
    assert "401" in str(error.value)
