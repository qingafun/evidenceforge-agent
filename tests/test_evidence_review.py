"""Evidence review decisions belong to exact report/source versions, not task status."""

import copy
import json

import httpx
import pytest

from evidenceforge.config import Settings
from evidenceforge.evidence_review import (
    agent_reviews,
    cited_ids,
    export_review_appendix,
    fingerprint,
    human_review_blocked,
    present_reviews,
    review_gate,
    review_summary,
    rule_reviews,
)
from evidenceforge.knowledge import KnowledgeBase
from evidenceforge.providers import ModelClient, ModelError
from evidenceforge.store import Store
from evidenceforge.workflow import Engine, citation_review


QUESTION = "Explain LangGraph checkpoint persistence"


@pytest.fixture
def review_state():
    return {"mode": "live", "question": QUESTION,
            "report_body": "LangGraph checkpoints preserve agent state. [ev_used]",
            "report": "LangGraph checkpoints preserve agent state. [ev_used]",
            "evidence": [
                {"id": "ev_used", "title": "Checkpoints", "source": "fixture:checkpoint",
                 "text": "LangGraph checkpoint persistence saves agent state."},
                {"id": "ev_unused", "title": "Operations", "source": "fixture:operations",
                 "text": "Checkpoints can be managed locally."},
            ]}


def decisions():
    return {"reviews": [
        {"evidence_id": "ev_used", "verdict": "supported", "relevant": True,
         "reason": "The stored-state statement matches the provided excerpt."},
        {"evidence_id": "ev_unused", "verdict": "not_used", "relevant": True,
         "reason": "This source is not cited in the report."},
    ]}


def test_agent_review_records_each_source_without_claiming_fact_verification(review_state):
    review_state["evidence_reviews"] = agent_reviews(QUESTION, review_state, decisions(), "fixture-model")
    for item in review_state["evidence"]:
        record = review_state["evidence_reviews"][item["id"]]
        assert record["fingerprint"] == fingerprint(QUESTION, review_state, item)
        assert record["human"] is None
        assert record["automatic"]["method"] == "agent"
        assert record["automatic"]["model"] == "fixture-model"
        assert record["automatic"]["reviewed_at"]
        assert record["automatic"]["reason"]
        assert "verified" not in record["automatic"]
    assert review_gate(QUESTION, review_state) == {"evidence_review_passed": True, "issues": []}
    assert review_summary(QUESTION, review_state) == {
        "total": 2, "agent": 2, "human": 0, "rules": 0, "pending": 0, "needs_attention": 0,
    }
    appendix = export_review_appendix(QUESTION, review_state)
    assert "Agent 已审阅" in appendix
    assert "不等于独立事实核验" in appendix
    assert "未用于报告" in appendix


@pytest.mark.parametrize("malformation", [
    "missing", "duplicate", "unknown", "cited_not_used", "empty_reason", "long_reason",
    "string_boolean", "missing_relevance", "invented_verdict", "extra_field", "wrong_root",
])
def test_malformed_review_cannot_mark_any_evidence_reviewed(review_state, malformation):
    payload = decisions()
    first = payload["reviews"][0]
    if malformation == "missing":
        payload["reviews"].pop()
    elif malformation == "duplicate":
        payload["reviews"].append(copy.deepcopy(first))
    elif malformation == "unknown":
        payload["reviews"][1]["evidence_id"] = "invented_source"
    elif malformation == "cited_not_used":
        first["verdict"] = "not_used"
    elif malformation == "empty_reason":
        first["reason"] = "   "
    elif malformation == "long_reason":
        first["reason"] = "x" * 2001
    elif malformation == "string_boolean":
        first["relevant"] = "true"
    elif malformation == "missing_relevance":
        first.pop("relevant")
    elif malformation == "invented_verdict":
        first["verdict"] = "verified"
    elif malformation == "extra_field":
        first["verified"] = True
    else:
        payload = {"passed": True}
    with pytest.raises(ModelError):
        agent_reviews(QUESTION, review_state, payload, "fixture-model")
    assert "evidence_reviews" not in review_state
    assert review_summary(QUESTION, review_state)["pending"] == 2
    assert not review_gate(QUESTION, review_state)["evidence_review_passed"]


@pytest.mark.parametrize(("verdict", "relevant"), [
    ("uncertain", True), ("contradicted", True), ("supported", False),
])
def test_cited_evidence_must_support_claim_and_be_relevant(review_state, verdict, relevant):
    payload = decisions()
    payload["reviews"][0].update(verdict=verdict, relevant=relevant)
    review_state["evidence_reviews"] = agent_reviews(QUESTION, review_state, payload, "fixture-model")
    result = review_gate(QUESTION, review_state)
    assert not result["evidence_review_passed"]
    assert len(result["issues"]) == 1
    assert "ev_used" in result["issues"][0]
    assert review_summary(QUESTION, review_state)["pending"] == 0


@pytest.mark.parametrize("changed", ["question", "report_body", "source", "text", "title"])
def test_changed_report_or_source_invalidates_prior_review(review_state, changed):
    review_state["evidence_reviews"] = agent_reviews(QUESTION, review_state, decisions(), "fixture-model")
    question = QUESTION
    if changed == "question":
        question += " and cleanup"
    elif changed == "report_body":
        review_state["report_body"] += " The state is permanent."
    else:
        review_state["evidence"][0][changed] += " changed"
    public = present_reviews(question, review_state)
    assert public["ev_used"]["automatic"] is None
    assert public["ev_used"]["human"] is None
    assert not review_gate(question, review_state)["evidence_review_passed"]


def test_final_report_wrappers_do_not_invalidate_reviews_or_count_bibliography_as_citations(review_state):
    review_state["evidence_reviews"] = agent_reviews(QUESTION, review_state, decisions(), "fixture-model")
    review_state["report"] = (
        "> 审阅状态：Agent 已审阅。\n\n" + review_state["report_body"]
        + "\n\n## 证据来源\n\n- [ev_unused] metadata\n\n## 证据审阅记录\nReview table."
    )
    assert cited_ids(review_state) == {"ev_used"}
    assert review_gate(QUESTION, review_state)["evidence_review_passed"]
    legacy = {"report": "Claim [ev_used]\n\n## 证据来源\n\n- [ev_unused] metadata"}
    assert cited_ids(legacy) == {"ev_used"}


@pytest.mark.parametrize("mode", ["live", "demo"])
def test_bibliography_only_citations_cannot_pass_evidence_or_citation_gate(review_state, mode):
    review_state["mode"] = mode
    report = "LangGraph checkpoints preserve agent state.\n\n## 证据来源\n\n- [ev_used] Checkpoints"
    review_state.update(report=report, report_body=report)
    if mode == "live":
        payload = decisions()
        for decision in payload["reviews"]:
            decision["verdict"] = "not_used"
        records = agent_reviews(QUESTION, review_state, payload, "fixture-model")
    else:
        records = rule_reviews(QUESTION, review_state)
    review_state["evidence_reviews"] = records
    assert not cited_ids(review_state)
    assert not review_gate(QUESTION, review_state)["evidence_review_passed"]
    citation_result = citation_review(report, review_state["evidence"])
    assert not citation_result["passed"]
    assert citation_result["citation_count"] == 0


def test_rule_review_does_not_pretend_to_review_semantic_support(review_state):
    review_state["mode"] = "demo"
    review_state["evidence_reviews"] = rule_reviews(QUESTION, review_state)
    used = review_state["evidence_reviews"]["ev_used"]["automatic"]
    assert used["method"] == "rules"
    assert used["verdict"] == "unchecked"
    assert used["relevant"] is None
    assert review_state["evidence_reviews"]["ev_unused"]["automatic"]["verdict"] == "not_used"
    assert review_summary(QUESTION, review_state)["agent"] == 0
    assert review_summary(QUESTION, review_state)["rules"] == 2
    assert review_gate(QUESTION, review_state)["evidence_review_passed"]
    review_state["mode"] = "live"
    assert not review_gate(QUESTION, review_state)["evidence_review_passed"]
    appendix = export_review_appendix(QUESTION, review_state)
    assert "规则已检查" in appendix
    assert "待语义审阅" in appendix
    assert "Agent 已审阅" not in appendix


def test_human_review_preserved_for_same_version_and_rejection_blocks_cited_source(review_state):
    review_state["evidence_reviews"] = agent_reviews(QUESTION, review_state, decisions(), "fixture-model")
    record = review_state["evidence_reviews"]["ev_used"]
    record["human"] = {"method": "human", "verdict": "contradicted", "reason": "The claim is too broad.",
                       "reviewed_at": "2026-09-30T00:00:00+00:00"}
    refreshed = agent_reviews(QUESTION, review_state, decisions(), "fixture-model")
    assert refreshed["ev_used"]["human"] == record["human"]
    assert human_review_blocked(QUESTION, review_state)
    assert review_summary(QUESTION, review_state)["human"] == 1
    assert review_summary(QUESTION, review_state)["needs_attention"] == 1
    review_state["report_body"] += " Revised after review."
    assert not human_review_blocked(QUESTION, review_state)


@pytest.fixture
def live_components(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="fixture-key", tavily_api_key="", model="fixture-model",
                        _env_file=None)
    settings.prepare()
    store = Store(tmp_path / "app.sqlite")
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite")
    knowledge.add_document("LangGraph checkpoint persistence", "LangGraph checkpoint persistence saves agent state.",
                           "fixture:persistence")
    selected_id = knowledge.search("LangGraph checkpoint")[0]["id"]
    return settings, store, knowledge, selected_id


def live_handler(selected_id, responses, calls):
    def handler(request):
        messages = json.loads(request.content)["messages"]
        role = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        message = {"role": "assistant", "content": ""}
        if "Role: Planner" in role:
            message["content"] = json.dumps({"objective": QUESTION, "questions": ["LangGraph checkpoint"],
                                             "strategy": "Read source excerpts"})
        elif "Role: Researcher" in role:
            if any(item["role"] == "tool" for item in messages):
                message["content"] = "Evidence collected."
            else:
                message["tool_calls"] = [{"id": "lookup", "type": "function", "function": {
                    "name": "search_knowledge", "arguments": '{"query":"LangGraph checkpoint"}'}}]
        elif "Role: Evidence selector" in role:
            message["content"] = json.dumps({"relevant_ids": [selected_id]})
        elif "Role: Analyst" in role:
            calls["writer"].append(payload)
            message["content"] = (f"# Checkpoints\n\nLangGraph checkpoint persistence saves agent state. [{selected_id}]"
                                  f"\n\nReport version {len(calls['writer'])}.")
        elif "Role: Evidence reviewer" in role:
            calls["reviewer"].append(payload)
            assert payload["question"] == QUESTION
            assert payload["cited_ids"] == [selected_id]
            assert [e["id"] for e in payload["evidence"]] == [selected_id]
            response = responses[-1] if len(responses) == 1 else responses.pop(0)
            message["content"] = json.dumps(response(payload))
        elif "Role: Critic" in role:
            message["content"] = json.dumps({"passed": True, "completeness_passed": True,
                "relevance_passed": True, "support_passed": True, "issues": [], "warnings": []})
        else:
            pytest.fail("Unexpected model role")
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 40, "completion_tokens": 20}})
    return handler


def source_decision(verdict, relevant=True):
    return lambda payload: {"reviews": [
        {"evidence_id": item["id"], "verdict": verdict, "relevant": relevant,
         "reason": "Fixture semantic review decision for the current report."}
        for item in payload["evidence"]
    ]}


@pytest.mark.parametrize(("verdict", "relevant"), [
    ("uncertain", True), ("contradicted", True), ("supported", False),
])
def test_negative_evidence_review_blocks_completion_then_retry_reviews_new_report(
    live_components, monkeypatch, verdict, relevant,
):
    settings, store, knowledge, selected_id = live_components
    responses = [source_decision(verdict, relevant)]
    calls = {"writer": [], "reviewer": []}
    with httpx.Client(transport=httpx.MockTransport(live_handler(selected_id, responses, calls))) as transport:
        monkeypatch.setattr("evidenceforge.workflow.ModelClient", lambda s: ModelClient(s, client=transport))
        run = store.create_run(QUESTION, "live", {"require_approval": False, "max_steps": 3, "remember": True})
        Engine(settings, store, knowledge).execute(run["id"])
        failed = store.get_run(run["id"])
        state = failed["state"]
        assert failed["status"] == "failed", failed.get("error")
        assert not state["review"]["evidence_review_passed"]
        assert not state["review"]["passed"]
        assert state["report_draft"]
        assert state["revision"] == 1
        assert state["evidence_reviews"][selected_id]["automatic"]["verdict"] == verdict
        assert len(calls["writer"]) == len(calls["reviewer"]) == 2
        assert not store.list_memories()
        old_fingerprint = state["evidence_reviews"][selected_id]["fingerprint"]

        responses[:] = [source_decision("supported")]
        Engine(settings, store, knowledge).execute(run["id"], resume=True)

    finished = store.get_run(run["id"])
    assert finished["status"] == "completed", finished.get("error")
    state = finished["state"]
    assert state["review"]["evidence_review_passed"]
    assert state["answer_status"] == "unverified"
    assert not state["report_draft"]
    assert "Agent 已审阅" in state["report"]
    assert "未核验" in state["report"]
    assert state["evidence_reviews"][selected_id]["fingerprint"] != old_fingerprint
    assert present_reviews(QUESTION, state)[selected_id]["automatic"]["verdict"] == "supported"
    assert calls["reviewer"][-1]["report"] == state["report_body"]
    assert len(calls["writer"]) == len(calls["reviewer"]) == 3
    assert len([e for e in store.events(run["id"]) if e["node"] == "research" and e["kind"] == "start"]) == 1
    assert len(store.list_memories()) == 1


def test_invalid_evidence_review_is_failed_and_can_resume_without_regenerating_report(live_components, monkeypatch):
    settings, store, knowledge, selected_id = live_components
    responses = [lambda _: {"reviews": []}]
    calls = {"writer": [], "reviewer": []}
    with httpx.Client(transport=httpx.MockTransport(live_handler(selected_id, responses, calls))) as transport:
        monkeypatch.setattr("evidenceforge.workflow.ModelClient", lambda s: ModelClient(s, client=transport))
        run = store.create_run(QUESTION, "live", {"require_approval": False, "max_steps": 3, "remember": False})
        Engine(settings, store, knowledge).execute(run["id"])
        failed = store.get_run(run["id"])
        assert failed["status"] == "failed"
        assert failed["state"]["report_draft"]
        assert all(review["automatic"] is None and review["human"] is None
                   for review in present_reviews(QUESTION, failed["state"]).values())
        assert review_summary(QUESTION, failed["state"])["pending"] == 1
        assert len(calls["writer"]) == len(calls["reviewer"]) == 1

        responses[:] = [source_decision("supported")]
        Engine(settings, store, knowledge).execute(run["id"], resume=True)

    assert store.get_run(run["id"])["status"] == "completed"
    assert len(calls["writer"]) == 1
    assert len(calls["reviewer"]) == 2


def test_offline_review_does_not_call_a_model_even_with_api_key_configured(live_components, monkeypatch):
    settings, store, knowledge, selected_id = live_components
    monkeypatch.setattr(ModelClient, "complete", lambda *_a, **_k: pytest.fail("Offline review called a model"))
    run = store.create_run(QUESTION, "demo", {"require_approval": False, "max_steps": 3, "remember": False})
    Engine(settings, store, knowledge).execute(run["id"])
    record = store.get_run(run["id"])
    assert record["status"] == "completed", record.get("error")
    state = record["state"]
    assert state["metrics"]["llm_calls"] == 0
    assert state["evidence_reviews"][selected_id]["automatic"]["method"] == "rules"
    assert state["evidence_reviews"][selected_id]["automatic"]["verdict"] == "unchecked"
    assert "规则已检查" in state["report"]
    assert "Agent 已审阅" not in state["report"]
    assert state["answer_status"] == "unverified"
