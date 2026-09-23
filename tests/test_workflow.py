"""Workflow integration tests; live mode uses an HTTP mock, never a paid model."""

import json

import httpx
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from evidenceforge.config import Settings
from evidenceforge.knowledge import KnowledgeBase
from evidenceforge.providers import ModelClient
from evidenceforge.store import Store
from evidenceforge.workflow import Engine, RunState, citation_review


PASS_REVIEW = {"passed": True, "completeness_passed": True, "relevance_passed": True,
               "support_passed": True, "issues": [], "warnings": []}


@pytest.fixture
def components(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="", tavily_api_key="", _env_file=None)
    settings.prepare()
    store = Store(tmp_path / "app.sqlite")
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite")
    knowledge.add_document("LangGraph persistence", "LangGraph checkpoint saves agent state. "
                           "人工审批可以暂停和恢复，拒绝后不执行工具。", "fixture:persistence")
    return settings, store, knowledge, Engine(settings, store, knowledge)


def create_run(store, **overrides):
    options = {"require_approval": True, "max_steps": 8, "remember": False} | overrides
    return store.create_run("比较 LangGraph 的持久化与人工审批方案", "demo", options)


def test_demo_plan_approval_report_memory_and_trace(components):
    _, store, _, engine = components
    run = create_run(store, remember=True)
    engine.execute(run["id"])
    waiting = store.get_run(run["id"])
    assert waiting["status"] == "awaiting_approval"
    assert waiting["state"]["plan"]["questions"]
    assert waiting["state"]["metrics"]["tool_calls"] == 0
    assert not store.list_memories()

    engine.execute(run["id"], approval={"approved": True, "feedback": "优先本地部署"})
    finished = store.get_run(run["id"])
    assert finished["status"] == "completed", finished.get("error")
    assert finished["state"]["review"]["passed"]
    assert finished["state"]["review"]["citation_validity"] == 1
    assert "离线演示" in finished["state"]["report"]
    assert finished["state"]["approval"]["feedback"] == "优先本地部署"
    assert finished["state"]["metrics"]["llm_calls"] == 0
    assert finished["state"]["metrics"]["tool_calls"] == 1
    assert len(store.list_memories()) == 1
    trace = store.events(run["id"])
    assert any(item["kind"] == "tool" for item in trace)
    assert trace[-1]["kind"] == "done"


def test_rejected_approval_never_executes_tools_or_writes_memory(components, monkeypatch):
    _, store, _, engine = components
    run = create_run(store, remember=True)
    monkeypatch.setattr(engine.registry, "execute", lambda *_: pytest.fail("Rejected plan invoked a tool"))
    engine.execute(run["id"])
    engine.execute(run["id"], approval={"approved": False, "feedback": "取消"})
    record = store.get_run(run["id"])
    assert record["status"] == "cancelled"
    assert not record["state"].get("report")
    assert not store.list_memories()
    assert record["state"]["metrics"]["tool_calls"] == 0


def test_cancelled_run_stays_cancelled_and_never_executes_tools(components, monkeypatch):
    _, store, _, engine = components
    run = create_run(store, require_approval=False)
    assert store.transition(run["id"], ("queued",), "cancelled")
    monkeypatch.setattr(engine.registry, "execute", lambda *_: pytest.fail("Cancelled run invoked a tool"))
    engine.execute(run["id"])
    assert store.get_run(run["id"])["status"] == "cancelled"
    assert not store.events(run["id"])


def test_cancellation_during_tool_stops_next_tool_and_report(components, monkeypatch):
    _, store, _, engine = components
    run = create_run(store, require_approval=False)
    calls = []
    original = engine.registry.execute

    def cancel_after_tool(name, arguments):
        calls.append(name)
        result = original(name, arguments)
        store.transition(run["id"], ("running",), "cancelled")
        return result

    monkeypatch.setattr(engine.registry, "execute", cancel_after_tool)
    engine.execute(run["id"])
    record = store.get_run(run["id"])
    assert calls == ["search_knowledge"]
    assert record["status"] == "cancelled"
    assert not record["state"].get("report")


def test_recreated_engine_resumes_persisted_approval_without_replanning(components):
    settings, store, knowledge, engine = components
    run = create_run(store)
    engine.execute(run["id"])
    replacement = Engine(settings, Store(settings.data_dir / "app.sqlite"), knowledge)
    replacement.execute(run["id"], approval={"approved": True, "feedback": ""})
    assert store.get_run(run["id"])["status"] == "completed"
    assert len([event for event in store.events(run["id"])
                if event["node"] == "plan" and event["kind"] == "start"]) == 1


def test_empty_knowledge_abstains_and_revision_is_bounded(components):
    settings, store, _, _ = components
    empty_knowledge = KnowledgeBase(settings.data_dir / "empty.sqlite")
    engine = Engine(settings, store, empty_knowledge)
    run = create_run(store, require_approval=False, max_steps=2)
    engine.execute(run["id"])
    record = store.get_run(run["id"])
    assert record["status"] == "failed"
    assert not record["state"]["review"]["passed"]
    assert record["state"]["answer_status"] == "insufficient_evidence"
    assert record["state"]["report_draft"]
    assert record["state"]["revision"] == 1
    assert "没有找到相关证据" in record["state"]["report"]
    assert record["state"]["metrics"]["tool_calls"] == 2


def test_citation_validation_distinguishes_missing_invalid_and_valid():
    evidence = [{"id": "ev_known"}]
    assert citation_review("A claim [ev_known]", evidence)["passed"]
    assert not citation_review("A claim without citation", evidence)["passed"]
    result = citation_review("A [ev_known] and B [ev_invented]", evidence)
    assert not result["passed"]
    assert result["citation_validity"] == 0.5
    assert not citation_review("Unsupported answer", [])["passed"]


def test_live_autonomous_tool_loop_with_mock_http_provider(components, monkeypatch):
    settings, store, knowledge, _ = components
    settings = settings.model_copy(update={"api_key": "mock-key", "model": "mock-model"})
    requests = []
    selected_id = knowledge.search("checkpoint")[0]["id"]

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        messages = payload["messages"]
        role = messages[0]["content"]
        message = {"role": "assistant", "content": "", "reasoning_content": "PRIVATE_REASONING"}
        if "Role: Planner" in role:
            message["content"] = json.dumps({"objective": "Compare state", "questions": ["checkpoint persistence"],
                                             "strategy": "Use evidence and inspect sources"})
        elif "Role: Researcher" in role:
            results = [m for m in messages if m["role"] == "tool"]
            if not results:
                message["tool_calls"] = [
                    {"id": "search-1", "type": "function", "function": {
                        "name": "search_knowledge", "arguments": '{"query":"checkpoint", "limit":2}'}},
                    {"id": "calc-1", "type": "function", "function": {
                        "name": "calculator", "arguments": '{"expression":"24 / 3"}'}},
                ]
            elif len(results) == 2:
                message["tool_calls"] = [{"id": "read-1", "type": "function", "function": {
                    "name": "read_source", "arguments": json.dumps({"id": selected_id})}}]
            else:
                message["content"] = "Evidence is sufficient."
        elif "Role: Analyst" in role:
            message["content"] = ("# Comparison\n\n| Option | Evidence |\n| --- | --- |\n"
                                  f"| Local state | Persist approval state locally. [{selected_id}] |")
        elif "Role: Evidence selector" in role:
            message["content"] = json.dumps({"relevant_ids": [selected_id]})
        elif "Role: Critic" in role:
            message["content"] = json.dumps(PASS_REVIEW)
        else:
            pytest.fail("Unexpected model role")
        return httpx.Response(200, json={"choices": [{"message": message}],
                                        "usage": {"prompt_tokens": 40, "completion_tokens": 20}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr("evidenceforge.workflow.ModelClient",
                            lambda settings: ModelClient(settings, client=transport))
        engine = Engine(settings, store, knowledge)
        run = store.create_run("比较可恢复的 Agent 审批方案", "live",
                               {"require_approval": False, "max_steps": 8, "remember": False})
        engine.execute(run["id"])
    record = store.get_run(run["id"])
    assert record["status"] == "completed", record.get("error")
    assert record["state"]["review"]["passed"]
    assert record["state"]["metrics"]["tool_calls"] == 3
    assert record["state"]["metrics"]["llm_calls"] == 7
    assert record["state"]["metrics"]["prompt_tokens"] == 280
    assert record["state"]["metrics"]["completion_tokens"] == 140
    tools = [event["data"]["tool"] for event in store.events(run["id"]) if event["kind"] == "tool"]
    assert tools == ["search_knowledge", "calculator", "read_source"]
    assert len(requests) == 7
    assert "PRIVATE_REASONING" not in json.dumps(store.events(run["id"]))
    assert "PRIVATE_REASONING" not in json.dumps(record)


def test_failed_writer_resumes_from_checkpoint_without_replaying_research(components, monkeypatch):
    settings, store, knowledge, _ = components
    settings = settings.model_copy(update={"api_key": "mock-key", "model": "mock-model"})
    selected_id = knowledge.search("checkpoint")[0]["id"]
    writer_attempts = []

    def handler(request):
        messages = json.loads(request.content)["messages"]
        role = messages[0]["content"]
        message = {"role": "assistant", "content": ""}
        if "Role: Planner" in role:
            message["content"] = '{"objective":"Research","questions":["checkpoint"],"strategy":"Find evidence"}'
        elif "Role: Researcher" in role:
            if any(item["role"] == "tool" for item in messages):
                message["content"] = "Enough evidence."
            else:
                message["tool_calls"] = [{"id": "search-1", "type": "function", "function": {
                    "name": "search_knowledge", "arguments": '{"query":"checkpoint"}'}}]
        elif "Role: Analyst" in role:
            writer_attempts.append(True)
            if len(writer_attempts) == 1:
                return httpx.Response(400, text="PRIVATE_PROVIDER_ERROR_BODY")
            message["content"] = f"Checkpoint state can be restored. [{selected_id}]"
        elif "Role: Evidence selector" in role:
            message["content"] = json.dumps({"relevant_ids": [selected_id]})
        elif "Role: Critic" in role:
            message["content"] = json.dumps(PASS_REVIEW)
        return httpx.Response(200, json={"choices": [{"message": message}],
                                        "usage": {"prompt_tokens": 40, "completion_tokens": 20}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr("evidenceforge.workflow.ModelClient",
                            lambda settings: ModelClient(settings, client=transport))
        run = store.create_run("Research persistent checkpoints", "live",
                               {"require_approval": False, "max_steps": 8, "remember": False})
        Engine(settings, store, knowledge).execute(run["id"])
        failed = store.get_run(run["id"])
        assert failed["status"] == "failed"
        assert "400" in failed["error"]
        assert "PRIVATE_PROVIDER_ERROR_BODY" not in failed["error"]
        assert failed["state"]["metrics"]["tool_calls"] == 1
        Engine(settings, Store(settings.data_dir / "app.sqlite"), knowledge).execute(run["id"], resume=True)

    restored = store.get_run(run["id"])
    assert restored["status"] == "completed", restored.get("error")
    assert restored["error"] is None
    assert restored["state"]["metrics"]["llm_calls"] == 7
    assert restored["state"]["metrics"]["tool_calls"] == 1
    trace = store.events(run["id"])
    assert len([event for event in trace if event["node"] == "research" and event["kind"] == "start"]) == 1
    assert len([event for event in trace if event["kind"] == "tool"]) == 1
    assert "PRIVATE_PROVIDER_ERROR_BODY" not in json.dumps(trace)


GENSHIN_QUESTION = "搜索原神中的国家与现实国家的对应关系"
# Deliberately unverified fixture claims; the test checks coverage/attribution, not game lore.
GENSHIN_SAMPLE = "蒙德:德国等欧洲文化\n\n璃月:中国文化\n\n稻妻:日本文化"


def test_demo_mapping_answers_from_relevant_import_and_excludes_technical_notes(components):
    _, store, knowledge, engine = components
    knowledge.add_document("原神中的国家与现实国家的对应关系", GENSHIN_SAMPLE, "用户导入，未核验")
    knowledge.add_document("研究关系与来源验证", "原型模型研究：比较国家数据搜索来源的 Agent 技术方案。", "fixture:unrelated")
    run = store.create_run(GENSHIN_QUESTION, "demo", {"require_approval": False, "max_steps": 4, "remember": False})
    engine.execute(run["id"])
    result = store.get_run(run["id"])
    assert result["status"] == "completed", result.get("error")
    state = result["state"]
    assert state["plan"]["questions"] == [GENSHIN_QUESTION]
    assert state["answer_contract"]["task_type"] == "mapping"
    assert state["answer_status"] == "unverified"
    assert not state["report_draft"]
    assert state["review"]["completeness_passed"]
    for name in ("蒙德", "璃月", "稻妻"):
        assert f"| {name} |" in state["report"]
    assert "未核验" in state["report"]
    assert "fixture:unrelated" not in state["report"]
    assert "fixture:persistence" not in state["report"]
    assert all("原神" in e["title"] for e in state["evidence"])


def test_demo_unknown_topic_does_not_fall_back_to_agent_boilerplate(components):
    _, store, _, engine = components
    run = store.create_run(GENSHIN_QUESTION, "demo", {"require_approval": False, "max_steps": 4, "remember": True})
    engine.execute(run["id"])
    record = store.get_run(run["id"])
    assert record["status"] == "failed"
    assert record["state"]["answer_status"] == "insufficient_evidence"
    assert not record["state"]["review"]["completeness_passed"]
    assert not record["state"]["evidence"]
    assert "LangGraph" not in record["state"]["report"]
    assert not store.list_memories()


@pytest.mark.parametrize("pending_node", ["write", "review", "finalize"])
def test_pre_upgrade_checkpoint_is_curated_before_answering(components, pending_node):
    settings, store, knowledge, engine = components
    run = create_run(store, require_approval=False)
    evidence = knowledge.search("LangGraph")
    legacy = StateGraph(RunState)
    legacy.add_node("research", lambda _: {"evidence": evidence, "revision": 0,
                    "plan": {"objective": run["question"], "questions": ["LangGraph"], "strategy": "search"},
                    "report": "旧版未验收报告"})

    def interrupted(_):
        raise RuntimeError("Simulated pre-upgrade interruption")

    legacy.add_node(pending_node, interrupted)
    legacy.add_edge(START, "research")
    legacy.add_edge("research", pending_node)
    legacy.add_edge(pending_node, END)
    config = {"configurable": {"thread_id": run["id"]}}
    with SqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite")) as saver:
        graph = legacy.compile(checkpointer=saver)
        with pytest.raises(RuntimeError, match="pre-upgrade"):
            graph.invoke(run["state"], config)
        old_state = graph.get_state(config).values
        assert "answer_contract" not in old_state
        store.update_run(run["id"], status="failed", state=old_state)
    engine.execute(run["id"], resume=True)
    result = store.get_run(run["id"])
    assert result["status"] == "completed", result.get("error")
    assert result["state"]["review"]["completeness_passed"]
    assert result["state"]["answer_contract"]["task_type"] == "comparison"
    assert "旧版未验收报告" not in result["state"]["report"]
    assert result["state"]["metrics"]["tool_calls"] == 0


@pytest.mark.parametrize("failure", ["missing_row", "cut_off", "abstain", "critic_blocking_issue",
                                     "missing_critic_checks", "null_issues", "null_warnings", "provider_cutoff"])
def test_live_answer_quality_is_mandatory_even_when_critic_says_passed(components, monkeypatch, failure):
    settings, store, knowledge, _ = components
    settings = settings.model_copy(update={"api_key": "mock", "model": "mock"})
    doc = knowledge.add_document("原神国家与现实文化", GENSHIN_SAMPLE, "自填的官方名称，未经核验")
    evidence_id = next(e["id"] for e in knowledge.search("原神") if e["document_id"] == doc["id"])
    fixed = False
    writer_calls = []

    def handler(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        role = messages[0]["content"]
        message = {"role": "assistant", "content": ""}
        if "Role: Planner" in role:
            message["content"] = json.dumps({"questions": ["原神"], "objective": GENSHIN_QUESTION,
                                             "strategy": "Search relevant sources", "question_type": "decision"})
        elif "Role: Researcher" in role:
            if any(m["role"] == "tool" for m in messages):
                message["content"] = "Collected evidence."
            else:
                message["tool_calls"] = [{"id": "search", "type": "function", "function": {
                    "name": "search_knowledge", "arguments": '{"query":"原神"}'}}]
        elif "Role: Evidence selector" in role:
            message["content"] = json.dumps({"relevant_ids": [evidence_id]})
        elif "Role: Analyst" in role:
            writer_calls.append(payload)
            rows = [("蒙德", "德国等欧洲文化"), ("璃月", "中国文化"), ("稻妻", "日本文化")]
            if failure == "missing_row" and not fixed:
                rows = rows[:2]
            message["content"] = "# 对应关系\n\n未核验的资料说法：\n\n| 国家 | 文化参考 |\n| --- | --- |\n" + "\n".join(
                f"| {name} | {value} [{evidence_id}] |" for name, value in rows)
            if failure == "cut_off" and not fixed:
                message["content"] += "\n\n它以概括"
            if failure == "abstain" and not fixed:
                message["content"] = f"# 建议\n\n没有官方核验，无法回答。[{evidence_id}]"
        elif "Role: Critic" in role:
            critique = dict(PASS_REVIEW)
            if failure == "critic_blocking_issue" and not fixed:
                critique["issues"] = ["尚未覆盖用户问题，需要补充条目。"]
            if failure == "missing_critic_checks" and not fixed:
                critique = {"passed": True, "issues": []}
            if failure == "null_issues" and not fixed:
                critique["issues"] = None
            if failure == "null_warnings" and not fixed:
                critique["warnings"] = None
            message["content"] = json.dumps(critique)
        else:
            pytest.fail("Unexpected model role")
        reason = "length" if failure == "provider_cutoff" and not fixed and "Role: Analyst" in role else "stop"
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": reason}],
                                        "usage": {"prompt_tokens": 40, "completion_tokens": 20}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr("evidenceforge.workflow.ModelClient", lambda s: ModelClient(s, client=transport))
        run = store.create_run(GENSHIN_QUESTION, "live", {"require_approval": False, "max_steps": 3, "remember": True})
        Engine(settings, store, knowledge).execute(run["id"])
        failed = store.get_run(run["id"])
        assert failed["status"] == "failed", failed.get("error")
        assert failed["state"]["report_draft"]
        assert len(writer_calls) == 2
        assert not store.list_memories()
        if failure == "provider_cutoff":
            assert not failed["state"].get("report")
            assert failed["state"]["generation"]["truncated"]
            assert failed["state"]["metrics"]["truncated_responses"] == 2
        else:
            assert not failed["state"]["review"]["passed"]
            assert failed["state"]["revision"] == 1
            assert "未通过验收的草稿" in failed["state"]["report"]

        # A retry of a terminal quality failure must really regenerate the answer.
        fixed = True
        Engine(settings, store, knowledge).execute(run["id"], resume=True)
    result = store.get_run(run["id"])
    assert result["status"] == "completed", result.get("error")
    assert result["state"]["review"]["completeness_passed"]
    assert result["state"]["answer_status"] == "unverified"
    assert not result["state"]["report_draft"]
    assert "未通过验收的草稿" not in result["state"]["report"]
    assert len(writer_calls) == 3
    assert writer_calls[-1]["max_tokens"] >= 8192
    assert result["state"]["generation"]["finish_reason"] == "stop"
    assert len([e for e in store.events(run["id"]) if e["node"] == "research" and e["kind"] == "start"]) == 1
    assert len(store.list_memories()) == 1
