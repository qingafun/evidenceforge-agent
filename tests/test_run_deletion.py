"""Deleting history removes one stopped run without damaging shared research data."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from evidenceforge.api import create_app
from evidenceforge.config import Settings


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


def complete_run(client, **options):
    response = client.post("/api/runs", json={
        "question": "如何使用 LangGraph 实现持久化审批？", "mode": "demo",
        "require_approval": False, **options,
    })
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    result = client.get(f"/api/runs/{run_id}").json()
    assert result["status"] == "completed", result.get("error")
    return result


def seed_stopped_run(app, status="completed"):
    run = app.state.store.create_run("研究 LangGraph 的检查点与审批", "demo", {})
    return app.state.store.update_run(run["id"], status=status,
                                      state={"report": "A saved research report."})


def checkpoint_counts(settings, run_id):
    with sqlite3.connect(settings.data_dir / "checkpoints.sqlite") as connection:
        return {table: connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE thread_id=?", (run_id,)).fetchone()[0]
                for table in ("checkpoints", "writes")}


def assert_deleted_endpoints(client, run_id, evidence_id="ev_missing", fingerprint="0" * 64):
    for suffix in ("", "/report", "/trace", "/events"):
        assert client.get(f"/api/runs/{run_id}{suffix}").status_code == 404
    for action, payload in (("resume", None), ("cancel", None), ("approve", {"approved": True})):
        assert client.post(f"/api/runs/{run_id}/{action}", json=payload).status_code == 404
    response = client.put(f"/api/runs/{run_id}/evidence/{evidence_id}/review", json={
        "fingerprint": fingerprint, "verdict": "supported", "reason": "A stale review cannot restore history.",
    })
    assert response.status_code == 404
    assert client.delete(f"/api/runs/{run_id}").status_code == 404


def test_delete_completed_demo_cleans_report_reviews_events_and_checkpoints(client, app, settings):
    run = complete_run(client, remember=True)
    other = complete_run(client)
    identifier = run["id"]
    evidence_id, review = next(iter(run["state"]["evidence_reviews"].items()))
    assert client.put(f"/api/runs/{identifier}/evidence/{evidence_id}/review", json={
        "fingerprint": review["fingerprint"], "verdict": "supported", "reason": "This source supports the report.",
    }).status_code == 200
    assert any(item["kind"] == "human_review" for item in app.state.store.events(identifier))
    assert all(checkpoint_counts(settings, identifier).values())
    other_counts = checkpoint_counts(settings, other["id"])
    other_trace = client.get(f"/api/runs/{other['id']}/trace").json()
    documents = client.get("/api/documents").json()
    memories = client.get("/api/memory").json()
    assert memories  # The explicitly enabled long-term memory is shared, not run history.

    deleted = client.delete(f"/api/runs/{identifier}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert app.state.store.get_run(identifier) is None
    assert app.state.store.events(identifier) == []
    assert checkpoint_counts(settings, identifier) == {"checkpoints": 0, "writes": 0}
    assert checkpoint_counts(settings, other["id"]) == other_counts
    assert client.get(f"/api/runs/{other['id']}").json() == other
    assert client.get(f"/api/runs/{other['id']}/trace").json() == other_trace
    assert client.get("/api/documents").json() == documents
    assert client.get("/api/memory").json() == memories
    assert [item["id"] for item in client.get("/api/runs").json()] == [other["id"]]
    assert_deleted_endpoints(client, identifier, evidence_id, review["fingerprint"])

    # A late event or queued executor must not resurrect deleted records.
    app.state.store.add_event(identifier, "system", "late", "Stale event")
    app.state.engine.execute(identifier)
    assert app.state.store.events(identifier) == []
    assert checkpoint_counts(settings, identifier) == {"checkpoints": 0, "writes": 0}


def test_deletion_survives_application_restart(settings):
    with TestClient(create_app(settings)) as first:
        run = complete_run(first)
        assert first.delete(f"/api/runs/{run['id']}").status_code == 204
    with TestClient(create_app(settings)) as restored:
        assert restored.get("/api/runs").json() == []
        assert_deleted_endpoints(restored, run["id"])
        assert checkpoint_counts(settings, run["id"]) == {"checkpoints": 0, "writes": 0}
        assert complete_run(restored)["status"] == "completed"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_delete_stopped_task_without_checkpoint_database(client, app, settings, status):
    run = seed_stopped_run(app, status)
    checkpoint_path = settings.data_dir / "checkpoints.sqlite"
    assert not checkpoint_path.exists()
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert not checkpoint_path.exists()


@pytest.mark.parametrize("status", ["queued", "running", "awaiting_approval"])
def test_active_tasks_cannot_be_deleted(client, app, status):
    run = app.state.store.create_run("等待停止后再删除研究记录", "demo", {})
    app.state.store.update_run(run["id"], status=status)
    app.state.store.add_event(run["id"], "plan", "start", "Keep this event")
    response = client.delete(f"/api/runs/{run['id']}")
    assert response.status_code == 409
    assert app.state.store.get_run(run["id"])["status"] == status
    assert len(app.state.store.events(run["id"])) == 1


def test_waiting_plan_can_be_cancelled_then_deleted(client, settings):
    response = client.post("/api/runs", json={"question": "比较 LangGraph 的持久化和审批方案"})
    run_id = response.json()["id"]
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
    assert checkpoint_counts(settings, run_id)["checkpoints"] > 0
    assert client.delete(f"/api/runs/{run_id}").status_code == 409
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert checkpoint_counts(settings, run_id) == {"checkpoints": 0, "writes": 0}


def test_delete_obeys_browser_same_origin_boundary(client, app):
    run = seed_stopped_run(app)
    url = f"/api/runs/{run['id']}"
    assert client.delete(url, headers={"origin": "https://other.example"}).status_code == 403
    assert client.get(url).status_code == 200
    assert client.delete(url, headers={"origin": "http://testserver"}).status_code == 204


def test_unknown_run_is_not_deletable(client):
    assert client.delete("/api/runs/not-a-real-run").status_code == 404


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_worker_lease_blocks_deletion_even_after_terminal_status(client, app, status):
    run = seed_stopped_run(app, status)
    with app.state.store.execution(run["id"]) as acquired:
        assert acquired
        assert client.delete(f"/api/runs/{run['id']}").status_code == 409
        assert client.get(f"/api/runs/{run['id']}").status_code == 200
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204


def test_cancelled_worker_must_finish_before_delete(client, app, monkeypatch):
    run = app.state.store.create_run("Cancellation must wait for the worker", "demo", {})
    started, finish = Event(), Event()

    def pending_worker(run_id, approval=None, resume=False):
        app.state.store.update_run(run_id, status="running")
        started.set()
        assert finish.wait(5)
        app.state.store.add_event(run_id, "system", "end", "Worker cleanup has finished.")

    monkeypatch.setattr(app.state.engine, "_execute", pending_worker)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(app.state.engine.execute, run["id"])
        try:
            assert started.wait(5)
            assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 200
            assert client.delete(f"/api/runs/{run['id']}").status_code == 409
        finally:
            finish.set()
        future.result(timeout=5)
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert app.state.store.events(run["id"]) == []


def test_worker_exception_releases_deletion_lock(client, app, monkeypatch):
    run = seed_stopped_run(app, "failed")

    def broken_worker(*args):
        raise RuntimeError("simulated worker exception")

    monkeypatch.setattr(app.state.engine, "_execute", broken_worker)
    with pytest.raises(RuntimeError, match="simulated worker exception"):
        app.state.engine.execute(run["id"])
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204


def test_duplicate_execution_does_not_release_original_worker_lease(client, app, monkeypatch):
    run = seed_stopped_run(app)
    calls = []
    monkeypatch.setattr(app.state.engine, "_execute", lambda *args: calls.append(args))
    with app.state.store.execution(run["id"]) as acquired:
        assert acquired
        app.state.engine.execute(run["id"])
        assert calls == []
        assert client.delete(f"/api/runs/{run['id']}").status_code == 409
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204


def test_storage_error_rolls_back_report_events_and_attached_checkpoints(client, app, settings):
    run = complete_run(client)
    run_id = run["id"]
    before_checkpoints = checkpoint_counts(settings, run_id)
    before_events = app.state.store.events(run_id)
    # Fail after checkpoint and event DELETEs to exercise the complete transaction.
    with app.state.store.connection() as connection:
        connection.execute("CREATE TRIGGER reject_run_deletion BEFORE DELETE ON runs "
                           "BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END")
    assert client.delete(f"/api/runs/{run_id}").status_code == 503
    assert client.get(f"/api/runs/{run_id}").json() == run
    assert app.state.store.events(run_id) == before_events
    assert checkpoint_counts(settings, run_id) == before_checkpoints
    with app.state.store.connection() as connection:
        connection.execute("DROP TRIGGER reject_run_deletion")
    assert client.delete(f"/api/runs/{run_id}").status_code == 204


def test_deletion_during_event_stream_finishes_with_deleted_status(client, app, settings, monkeypatch):
    run = seed_stopped_run(app)
    original_events = app.state.store.events
    removed = False

    def remove_on_stream_read(run_id, after=0):
        nonlocal removed
        if not removed:
            removed = True
            app.state.store.delete_run(run_id, settings.data_dir / "checkpoints.sqlite")
        return original_events(run_id, after)

    monkeypatch.setattr(app.state.store, "events", remove_on_stream_read)
    response = client.get(f"/api/runs/{run['id']}/events")
    assert response.status_code == 200
    assert 'event: status\ndata: {"status": "deleted"}' in response.text
    assert app.state.store.get_run(run["id"]) is None
