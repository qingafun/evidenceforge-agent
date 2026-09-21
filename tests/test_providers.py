import json
from types import SimpleNamespace

import httpx
import pytest

from evidenceforge.providers import BudgetExceeded, ModelClient, ModelError, parse_json


def settings(**kwargs):
    values = dict(api_key="test-only-secret", base_url="https://provider.example/v1",
                  model="test-model", request_timeout=5, max_tokens=24000)
    return SimpleNamespace(**(values | kwargs))


def reply(content="A grounded result", **kwargs):
    return {"choices": [{"message": {"content": content, "role": "assistant"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 12}} | kwargs


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_native_tools_json_mode_usage_and_no_reasoning_exposure():
    observed = []
    callbacks = []

    def handler(request):
        observed.append(json.loads(request.content))
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only-secret"
        body = reply(None)
        body["choices"][0]["message"].update(
            reasoning_content="Do not persist private reasoning",
            tool_calls=[{"id": "call_1", "type": "function", "function": {
                "name": "search_knowledge", "arguments": '{"query":"memory"}',
            }}],
        )
        return httpx.Response(200, json=body)

    with mock_client(handler) as transport:
        model = ModelClient(settings(), callbacks.append, client=transport)
        result = model.complete([{"role": "user", "content": "Research memory"}],
                                tools=[{"type": "function", "function": {"name": "search_knowledge"}}],
                                json_mode=True)
    assert result["content"] == ""
    assert result["tool_calls"][0]["function"]["name"] == "search_knowledge"
    assert "reasoning_content" not in result
    assert observed[0]["max_tokens"] == 2000
    assert observed[0]["response_format"] == {"type": "json_object"}
    assert observed[0]["tool_choice"] == "auto"
    assert callbacks[0]["prompt_tokens"] == 50
    assert callbacks[0]["completion_tokens"] == 12
    assert not callbacks[0]["estimated_usage"]


def test_budget_preflight_makes_no_network_request():
    def handler(_):
        pytest.fail("An over-budget request must never reach the provider")

    with mock_client(handler) as transport:
        model = ModelClient(settings(max_tokens=20), client=transport)
        with pytest.raises(BudgetExceeded):
            model.complete([{"role": "user", "content": "larger than the budget"}])
    assert model.metrics["llm_calls"] == 0


def test_cumulative_budget_and_requested_output_reservation():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=reply(usage={"prompt_tokens": 100, "completion_tokens": 90}))

    with mock_client(handler) as transport:
        model = ModelClient(settings(max_tokens=200), client=transport)
        model.complete([{"role": "user", "content": "a"}])
        with pytest.raises(BudgetExceeded):
            model.complete([{"role": "user", "content": "a"}])
    assert len(requests) == 1
    assert 0 < requests[0]["max_tokens"] < 200
    assert model.metrics["prompt_tokens"] + model.metrics["completion_tokens"] == 190


def test_missing_usage_is_estimated_and_charged():
    with mock_client(lambda _: httpx.Response(200, json=reply(usage=None))) as transport:
        model = ModelClient(settings(), client=transport)
        model.complete([{"role": "user", "content": "你好"}])
    assert model.metrics["estimated_usage"]
    assert model.metrics["prompt_tokens"] > 0
    assert model.metrics["completion_tokens"] > 0


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_errors_retry_twice_and_account_for_failed_attempts(status, monkeypatch):
    monkeypatch.setattr("evidenceforge.providers.time.sleep", lambda _: None)
    calls = []

    def handler(_):
        calls.append(True)
        return httpx.Response(status, text="SECRET_PROVIDER_BODY") if len(calls) < 3 else httpx.Response(200, json=reply())

    with mock_client(handler) as transport:
        model = ModelClient(settings(), client=transport)
        model.complete([{"role": "user", "content": "hello"}])
    assert model.metrics["llm_calls"] == 3
    assert model.metrics["failed_attempts"] == 2
    assert model.metrics["retries"] == 2
    assert model.metrics["estimated_usage"]
    assert model.metrics["completion_tokens"] == 4012


def test_timeout_retry_limit_and_sanitized_exception(monkeypatch):
    monkeypatch.setattr("evidenceforge.providers.time.sleep", lambda _: None)

    def handler(request):
        raise httpx.ReadTimeout("SECRET_TIMEOUT_DETAIL", request=request)

    with mock_client(handler) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError) as error:
            model.complete([{"role": "user", "content": "hello"}])
    assert "SECRET" not in str(error.value)
    assert model.metrics["llm_calls"] == 3


def test_nontransient_error_is_not_retried_or_exposed():
    with mock_client(lambda _: httpx.Response(401, text="test-only-secret SECRET_BODY")) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError) as error:
            model.complete([{"role": "user", "content": "hello"}])
    assert "401" in str(error.value)
    assert "secret" not in str(error.value)
    assert "SECRET" not in str(error.value)
    assert model.metrics["llm_calls"] == 1


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {"tool_calls": [{}]}}]}])
def test_malformed_responses_are_sanitized_and_not_retried(body):
    with mock_client(lambda _: httpx.Response(200, json=body)) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError, match="invalid chat-completions"):
            model.complete([{"role": "user", "content": "hello"}])
    assert model.metrics["llm_calls"] == 1
    assert model.metrics["estimated_usage"]


def test_retry_cannot_bypass_budget(monkeypatch):
    monkeypatch.setattr("evidenceforge.providers.time.sleep", lambda _: None)
    with mock_client(lambda _: httpx.Response(503)) as transport:
        model = ModelClient(settings(max_tokens=1000), client=transport)
        with pytest.raises(BudgetExceeded):
            model.complete([{"role": "user", "content": "hello"}])
    assert model.metrics["llm_calls"] == 1


@pytest.mark.parametrize("content", ['{"ok": true}', '```json\n{"ok": true}\n```', '```\n{"ok":true}\n```'])
def test_parse_json_valid(content):
    assert parse_json(content) == {"ok": True}


@pytest.mark.parametrize("content", ["__import__('os').system('bad')", "prefix {\"ok\":true}", "```python\n{}\n```", "{oops}"])
def test_parse_json_rejects_executable_or_malformed_content(content):
    with pytest.raises(ModelError):
        parse_json(content)
