"""Human judgments are versioned, durable, and cannot promote failed reports."""

import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from evidenceforge.api import create_app
from evidenceforge.config import Settings
from evidenceforge.evidence_review import fingerprint
from evidenceforge.store import Store


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


def seed_run(app, *, status="completed", passed=True, automatic=False):
    question = "原神中的璃月与哪个现实国家相关？"
    run = app.state.store.create_run(question, "demo", {})
    state = {"report": "璃月以中国文化为主要灵感。[ev_one]", "review": {"passed": passed},
             "report_draft": not passed, "answer_status": "unverified", "evidence": [
                 {"id": "ev_one", "title": "璃月", "text": "璃月参考中国文化。", "source": "local:first"},
                 {"id": "ev_two", "title": "蒙德", "text": "蒙德参考欧洲文化。", "source": "local:second"},
             ]}
    if automatic:
        state["report_body"] = state["report"]
        state["evidence_reviews"] = {
            item["id"]: {"fingerprint": fingerprint(question, state, item), "human": None,
                         "automatic": {"method": "agent", "verdict": "supported", "relevant": True,
                                       "reason": "与当前引用一致。", "reviewed_at": "2026-09-30T00:00:00+00:00",
                                       "model": "test-model"}}
            for item in state["evidence"]}
    return app.state.store.update_run(run["id"], status=status, state=state)


def submit(client, run, evidence="ev_one", verdict="supported", reason="已阅读原文，能够支持引用。", **extra):
    current = client.get(f"/api/runs/{run['id']}").json()
    version = current["state"]["evidence_reviews"].get(evidence, {}).get("fingerprint", "0" * 64)
    body = {"fingerprint": version, "verdict": verdict, "reason": reason, **extra}
    return client.put(f"/api/runs/{run['id']}/evidence/{evidence}/review", json=body)


def test_old_runs_have_pending_records_on_read_and_list(app, client):
    run = seed_run(app)
    for result in (client.get(f"/api/runs/{run['id']}").json(), client.get("/api/runs").json()[0]):
        reviews = result["state"]["evidence_reviews"]
        assert set(reviews) == {"ev_one", "ev_two"}
        assert all(len(item["fingerprint"]) == 64 for item in reviews.values())
        assert all(item["automatic"] is None and item["human"] is None for item in reviews.values())
        assert result["state"]["human_review_blocked"] is False
    assert "待审阅" in client.get(f"/api/runs/{run['id']}/report").text


def test_human_review_persists_and_audits_without_replacing_agent(app, client, settings):
    run = seed_run(app, automatic=True)
    response = submit(client, run)
    assert response.status_code == 200, response.text
    saved = response.json()["state"]["evidence_reviews"]["ev_one"]
    assert saved["automatic"] == run["state"]["evidence_reviews"]["ev_one"]["automatic"]
    assert saved["human"]["method"] == "human"
    assert saved["human"]["verdict"] == "supported"
    assert "当前证据审阅：人工已审阅 1 条" in client.get(f"/api/runs/{run['id']}/report").text
    assert datetime.fromisoformat(saved["human"]["reviewed_at"]).utcoffset().total_seconds() == 0
    with TestClient(create_app(settings)) as restored:
        result = restored.get(f"/api/runs/{run['id']}").json()
        assert result["state"]["evidence_reviews"]["ev_one"] == saved
        trace = restored.get(f"/api/runs/{run['id']}/trace").json()
        assert trace[-1]["kind"] == "human_review"
        assert trace[-1]["data"]["review"] == saved["human"]
        assert trace[-1]["data"]["fingerprint"] == saved["fingerprint"]
        assert trace[-1]["data"]["previous"] is None
        changed = submit(restored, result, verdict="uncertain", reason="来源表述尚有歧义。")
        assert changed.status_code == 200
        trace = restored.get(f"/api/runs/{run['id']}/trace").json()
        assert trace[-1]["data"]["previous"] == saved["human"]


@pytest.mark.parametrize("verdict", ["uncertain", "contradicted"])
def test_negative_cited_review_blocks_export_without_rewriting_quality(app, client, verdict):
    run = seed_run(app)
    response = submit(client, run, verdict=verdict)
    result = response.json()
    assert result["status"] == "completed"
    assert result["state"]["review"]["passed"] is True
    assert result["state"]["report_draft"] is False
    assert result["state"]["human_review_blocked"] is True
    exported = client.get(f"/api/runs/{run['id']}/report")
    assert "-draft.md" in exported.headers["content-disposition"]
    assert "人工审阅提示" in exported.text
    assert "未通过验收的草稿" in exported.text
    assert "人工已审阅" in exported.text
    assert "不等于独立事实核验" in exported.text
    assert app.state.store.get_run(run["id"])["state"]["report"] == run["state"]["report"]
    assert submit(client, run).json()["state"]["human_review_blocked"] is False
    assert "-draft.md" not in client.get(f"/api/runs/{run['id']}/report").headers["content-disposition"]


def test_uncited_review_does_not_block_report(app, client):
    run = seed_run(app)
    response = submit(client, run, evidence="ev_two", verdict="contradicted")
    assert response.status_code == 200
    assert response.json()["state"]["human_review_blocked"] is False
    assert "-draft.md" not in client.get(f"/api/runs/{run['id']}/report").headers["content-disposition"]


def test_manual_support_cannot_promote_original_failed_report(app, client):
    run = seed_run(app, status="failed", passed=False)
    result = submit(client, run).json()
    assert result["status"] == "failed"
    assert result["state"]["review"]["passed"] is False
    assert result["state"]["report_draft"] is True
    assert "-draft.md" in client.get(f"/api/runs/{run['id']}/report").headers["content-disposition"]


@pytest.mark.parametrize("status", ["queued", "running", "awaiting_approval"])
def test_busy_or_awaiting_approval_rejects_human_review(app, client, status):
    run = seed_run(app, status=status)
    assert submit(client, run).status_code == 409
    assert not app.state.store.events(run["id"])


def test_missing_report_and_unknown_entities(app, client):
    run = seed_run(app)
    assert submit(client, run, evidence="ev_missing").status_code == 404
    response = client.put("/api/runs/missing/evidence/ev_one/review", json={
        "fingerprint": "0" * 64, "verdict": "supported", "reason": "已读。"})
    assert response.status_code == 404
    app.state.store.update_run(run["id"], state={"report": ""})
    assert submit(client, run).status_code == 409


@pytest.mark.parametrize("change", ["report", "question", "evidence"])
def test_changed_material_invalidates_reviews_and_rejects_stale_submit(app, client, change):
    run = seed_run(app, automatic=True)
    assert submit(client, run).status_code == 200
    version = client.get(f"/api/runs/{run['id']}").json()["state"]["evidence_reviews"]["ev_one"]["fingerprint"]
    if change == "report":
        app.state.store.update_run(run["id"], state={"report_body": "另一条结论。[ev_one]"})
    elif change == "question":
        with app.state.store.connection() as conn:
            conn.execute("UPDATE runs SET question=? WHERE id=?", ("修改后的研究问题", run["id"]))
    else:
        changed = copy.deepcopy(run["state"]["evidence"])
        changed[0]["text"] += " 补充了新的限制条件。"
        app.state.store.update_run(run["id"], state={"evidence": changed})
    current = client.get(f"/api/runs/{run['id']}").json()["state"]["evidence_reviews"]["ev_one"]
    assert current["fingerprint"] != version
    assert current["automatic"] is None and current["human"] is None
    assert submit(client, run, fingerprint=version).status_code == 409
    assert len(app.state.store.events(run["id"])) == 1


@pytest.mark.parametrize("extra", [
    {"method": "agent"}, {"model": "forged"}, {"reviewed_at": "yesterday"},
    {"reason": " "}, {"reason": "x" * 2001}, {"verdict": "verified"}, {"fingerprint": "invalid"},
])
def test_client_cannot_forge_review_provenance_or_invalid_fields(app, client, extra):
    run = seed_run(app)
    assert submit(client, run, **extra).status_code == 422
    assert not app.state.store.events(run["id"])


def test_untrusted_reason_stays_json_and_exports_escaped(app, client):
    run = seed_run(app)
    payload = '<script>alert("xss")</script> | [link](javascript:evil)\n# forged heading'
    response = submit(client, run, reason=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json()["state"]["evidence_reviews"]["ev_one"]["human"]["reason"] == payload
    exported = client.get(f"/api/runs/{run['id']}/report").text
    assert "<script>" not in exported
    assert "\n# forged heading" not in exported
    assert "\\<script\\>" in exported


def test_parallel_reviews_do_not_lose_other_evidence_decisions(app, client):
    run = seed_run(app, automatic=True)
    barrier = Barrier(2)

    def review(identifier):
        separate_store = Store(app.state.store.path)
        version = run["state"]["evidence_reviews"][identifier]["fingerprint"]
        barrier.wait(timeout=5)
        separate_store.review_evidence(run["id"], identifier, version, "supported", f"已核对 {identifier}。")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(review, ["ev_one", "ev_two"]))
    current = client.get(f"/api/runs/{run['id']}").json()
    assert all(item["human"]["verdict"] == "supported" for item in current["state"]["evidence_reviews"].values())
    assert len(app.state.store.events(run["id"])) == 2


def test_late_cancelled_worker_preserves_current_human_review(app, client):
    run = seed_run(app, status="cancelled", automatic=True)
    stale = copy.deepcopy(run["state"])
    saved = submit(client, run, verdict="contradicted").json()["state"]["evidence_reviews"]["ev_one"]["human"]
    app.state.store.update_run(run["id"], state=stale)
    current = client.get(f"/api/runs/{run['id']}").json()
    assert current["state"]["evidence_reviews"]["ev_one"]["human"] == saved
    assert current["state"]["human_review_blocked"] is True


def test_review_and_audit_are_one_transaction(app, client):
    run = seed_run(app)
    with app.state.store.connection() as conn:
        conn.execute("CREATE TRIGGER fail_review_audit BEFORE INSERT ON events "
                     "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        submit(client, run)
    current = client.get(f"/api/runs/{run['id']}").json()["state"]["evidence_reviews"]["ev_one"]
    assert current["human"] is None
