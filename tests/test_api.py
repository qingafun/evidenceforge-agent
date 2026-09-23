"""HTTP contract, restart, SSE, and data-boundary integration tests."""

import json

import pytest
from fastapi.testclient import TestClient

from evidenceforge.api import create_app
from evidenceforge.bootstrap import seed_demo_once
from evidenceforge.config import Settings
from evidenceforge.knowledge import KnowledgeBase
from evidenceforge.mcp_server import build_server


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, api_key="", model="your-model-name", tavily_api_key="", _env_file=None)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def start_run(client, **options):
    response = client.post("/api/runs", json={"question": "如何使用 LangGraph 实现持久化审批？", **options})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_health_and_missing_live_configuration(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["knowledge"]["documents"] > 0
    assert not health.json()["live_available"]
    assert "search_web" not in health.json()["tools"]
    response = client.post("/api/runs", json={"question": "Research checkpoints", "mode": "live"})
    assert response.status_code == 422
    assert client.get("/api/runs").json() == []


def test_offline_api_docs_and_openapi_are_served_locally(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert docs.headers["content-type"].startswith("text/html")
    assert "/static/api.js" in docs.text
    assert "cdn.jsdelivr.net" not in docs.text
    assert "unpkg.com" not in docs.text
    script = client.get("/static/api.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "/openapi.json" in script.text
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["openapi"].startswith("3.")
    assert "/api/runs" in schema.json()["paths"]
    assert "/api/runs/{run_id}/approve" in schema.json()["paths"]


def test_complete_http_flow_sse_reconnect_and_report(client):
    run_id = start_run(client)
    state = client.get(f"/api/runs/{run_id}").json()
    assert state["status"] == "awaiting_approval"
    assert client.get(f"/api/runs/{run_id}/report").status_code == 409
    response = client.post(f"/api/runs/{run_id}/approve", json={"approved": True, "feedback": "优先 SQLite"})
    assert response.status_code == 200
    finished = client.get(f"/api/runs/{run_id}").json()
    assert finished["status"] == "completed", finished.get("error")
    report = client.get(f"/api/runs/{run_id}/report")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/markdown")
    assert "attachment" in report.headers["content-disposition"]
    assert "离线演示" in report.text

    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert any(item["kind"] == "tool" for item in trace)
    first_id = trace[0]["id"]
    assert client.get(f"/api/runs/{run_id}/trace?after={first_id}").json() == trace[1:]
    stream = client.get(f"/api/runs/{run_id}/events", headers={"last-event-id": str(first_id)})
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert f"id: {first_id}\n" not in stream.text
    assert f"id: {trace[-1]['id']}\n" in stream.text
    assert "event: status" in stream.text
    assert '"status": "completed"' in stream.text
    event_data = [json.loads(line.removeprefix("data: ")) for line in stream.text.splitlines()
                  if line.startswith("data: ")]
    assert len(event_data) == len(trace)


def test_waiting_approval_survives_new_app_instance(settings):
    first_app = create_app(settings)
    with TestClient(first_app) as client:
        run_id = start_run(client)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
    with TestClient(create_app(settings)) as restored:
        assert restored.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
        assert restored.post(f"/api/runs/{run_id}/approve", json={"approved": True}).status_code == 200
        assert restored.get(f"/api/runs/{run_id}").json()["status"] == "completed"
        trace = restored.get(f"/api/runs/{run_id}/trace").json()
        assert len([event for event in trace if event["node"] == "plan" and event["kind"] == "start"]) == 1


def test_insufficient_evidence_is_failed_and_export_is_labeled_draft(client):
    response = client.post("/api/runs", json={"question": "搜索原神中的国家与现实国家的对应关系",
                                            "mode": "demo", "require_approval": False})
    assert response.status_code == 201
    run_id = response.json()["id"]
    result = client.get(f"/api/runs/{run_id}").json()
    assert result["status"] == "failed"
    assert result["state"]["answer_status"] == "insufficient_evidence"
    assert result["state"]["review"]["completeness_passed"] is False
    report = client.get(f"/api/runs/{run_id}/report")
    assert report.status_code == 200
    assert "-draft.md" in report.headers["content-disposition"]
    assert "未通过验收的草稿" in report.text
    assert "LangGraph" not in report.text


def test_restart_marks_running_task_failed_and_resume_completes(settings):
    initial = create_app(settings)
    run = initial.state.store.create_run("研究 LangGraph checkpoint", "demo",
        {"require_approval": False, "max_steps": 8, "remember": False})
    initial.state.store.update_run(run["id"], status="running")
    with TestClient(create_app(settings)) as restored:
        interrupted = restored.get(f"/api/runs/{run['id']}").json()
        assert interrupted["status"] == "failed"
        assert "重启" in interrupted["error"]
        assert restored.post(f"/api/runs/{run['id']}/resume").status_code == 200
        assert restored.get(f"/api/runs/{run['id']}").json()["status"] == "completed"


def test_reject_cancel_and_invalid_transitions(client):
    rejected = start_run(client)
    assert client.post(f"/api/runs/{rejected}/approve", json={"approved": False}).status_code == 200
    assert client.get(f"/api/runs/{rejected}").json()["status"] == "cancelled"
    assert client.post(f"/api/runs/{rejected}/approve", json={"approved": True}).status_code == 409
    assert client.post(f"/api/runs/{rejected}/resume").status_code == 409
    assert client.post(f"/api/runs/{rejected}/cancel").status_code == 409

    cancelled = start_run(client)
    assert client.post(f"/api/runs/{cancelled}/resume").status_code == 409
    assert client.post(f"/api/runs/{cancelled}/cancel").status_code == 200
    assert client.post(f"/api/runs/{cancelled}/approve", json={"approved": True}).status_code == 409
    trace = client.get(f"/api/runs/{cancelled}/trace").json()
    assert not any(item["kind"] == "tool" for item in trace)

    completed = start_run(client, require_approval=False)
    assert client.post(f"/api/runs/{completed}/cancel").status_code == 409
    assert client.post(f"/api/runs/{completed}/resume").status_code == 409
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_document_and_memory_crud_preserve_untrusted_data_as_json(client, app):
    payload = '<script>alert("xss")</script>'
    document = {"title": payload, "content": "Untrusted research content " + payload,
                "source": "javascript:alert(1)"}
    response = client.post("/api/documents", json=document)
    assert response.status_code == 201
    document_id = response.json()["id"]
    listing = client.get("/api/documents")
    assert listing.headers["content-type"].startswith("application/json")
    assert listing.headers["x-content-type-options"] == "nosniff"
    assert any(item["id"] == document_id and item["title"] == payload for item in listing.json())
    chunks = app.state.knowledge.search("Untrusted research content")
    assert any(payload in chunk["text"] for chunk in chunks)
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.delete(f"/api/documents/{document_id}").status_code == 404

    memory = client.post("/api/memory", json={"content": payload})
    assert memory.status_code == 201
    memory_id = memory.json()["id"]
    assert client.get("/api/memory").json()[0]["content"] == payload
    assert client.post("/api/memory", json={"content": payload}).json()["id"] == memory_id
    assert client.delete(f"/api/memory/{memory_id}").status_code == 204
    assert client.delete(f"/api/memory/{memory_id}").status_code == 404
    assert client.get("/api/memory").json() == []


def test_browser_write_boundaries_and_validation(client):
    response = client.post("/api/memory", json={"content": "test"}, headers={"origin": "https://other.example"})
    assert response.status_code == 403
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.post("/api/memory", json={"content": "ok"}, headers={"origin": "http://testserver"}).status_code == 201
    assert client.post("/api/runs", json={"question": "x"}).status_code == 422
    assert client.post("/api/runs", json={"question": "a valid query", "max_steps": 100}).status_code == 422
    assert client.post("/api/runs", json={"question": "a valid query", "extra": True}).status_code == 422
    assert client.post("/api/documents", json={"title": "a", "content": "tiny"}).status_code == 422
    assert "frame-ancestors 'none'" in client.get("/api/health").headers["content-security-policy"]


def test_deleted_demo_documents_stay_deleted_across_app_and_mcp_restarts(settings):
    initial = create_app(settings)
    with TestClient(initial) as client:
        documents = client.get("/api/documents").json()
        assert len(documents) == 10
        removed_id = documents[0]["id"]
        assert client.delete(f"/api/documents/{removed_id}").status_code == 204

    build_server(settings)
    restored = create_app(settings)
    with TestClient(restored) as client:
        documents = client.get("/api/documents").json()
        assert len(documents) == 9
        assert removed_id not in {document["id"] for document in documents}
        for document in documents:
            assert client.delete(f"/api/documents/{document['id']}").status_code == 204

    # Empty is also an intentional user state, not a request to reset the demo.
    build_server(settings)
    with TestClient(create_app(settings)) as final:
        assert final.get("/api/documents").json() == []
        assert final.get("/api/health").json()["knowledge"] == {"documents": 0, "chunks": 0}


def test_interrupted_bootstrap_retries_remaining_documents(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "one.md").write_text("# One\nFirst example content.", encoding="utf-8")
    (corpus / "two.md").write_text("# Two\nSecond example content.", encoding="utf-8")
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite")
    original = knowledge.seed

    def interrupted(directory):
        knowledge.add_document("One", (directory / "one.md").read_text(encoding="utf-8"), "demo:one.md")
        raise RuntimeError("Simulated interruption during initial import")

    monkeypatch.setattr(knowledge, "seed", interrupted)
    with pytest.raises(RuntimeError, match="Simulated interruption"):
        seed_demo_once(knowledge, corpus)
    assert knowledge.stats()["documents"] == 1
    monkeypatch.setattr(knowledge, "seed", original)
    assert seed_demo_once(knowledge, corpus) == 1
    assert knowledge.stats()["documents"] == 2
    assert seed_demo_once(knowledge, corpus) == 0


def test_existing_demo_database_migrates_without_restoring_removed_examples(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "one.md").write_text("# One\nFirst example content.", encoding="utf-8")
    (corpus / "two.md").write_text("# Two\nSecond example content.", encoding="utf-8")
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite")
    assert knowledge.seed(corpus) == 2  # Database created by the previous startup behavior.
    removed = knowledge.list_documents()[0]["id"]
    knowledge.delete_document(removed)
    assert seed_demo_once(knowledge, corpus) == 0
    assert knowledge.stats()["documents"] == 1
    assert removed not in {document["id"] for document in knowledge.list_documents()}
