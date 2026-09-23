"""Observable answer quality is a mandatory gate, separate from source truth."""

import pytest

from evidenceforge.quality import (
    answer_contract,
    check_answer,
    classify_question,
    filter_evidence,
    mapping_pairs,
)


@pytest.fixture
def cultural_evidence():
    # Authored sample claims for structure tests, not independently verified facts.
    return [{"id": "ev_cultures", "title": "原神国家对应关系", "source": "用户资料",
             "text": "蒙德:德国（资料中的未核验说法）\n\n璃月:中国（资料中的未核验说法）\n\n稻妻:日本"}]


@pytest.mark.parametrize(("question", "expected"), [
    ("搜索原神中的国家与现实国家的对应关系", "mapping"),
    ("搜寻原神的各个国家分别来自于现实的那些国家。", "mapping"),
    ("Map HTTP status codes to their meanings", "mapping"),
    ("比较 LangGraph 与 CrewAI 的持久化方案", "comparison"),
    ("How to run the project locally?", "howto"),
    ("推荐适合本地知识库的技术选型", "decision"),
    ("Explain reciprocal rank fusion", "explanation"),
    ("Who wrote this library?", "factual"),
])
def test_classify_question(question, expected):
    assert classify_question(question) == expected


def test_irrelevant_technical_documents_are_rejected(cultural_evidence):
    unrelated = [{"id": "ev_tech", "title": "Agent 评估与原型", "source": "原神官方",
                  "text": "官方来源不代表模型输出正确。原型的评测需要检查引用与证据。"}]
    kept, rejected = filter_evidence("搜索原神中的国家与现实国家的对应关系", cultural_evidence + unrelated)
    assert kept == cultural_evidence
    assert [item["id"] for item in rejected] == ["ev_tech"]
    assert rejected[0]["rejection_reason"]
    assert "rejection_reason" not in unrelated[0]


def test_relevance_filter_keeps_supporting_technical_component():
    evidence = [{"id": "ev_1", "title": "人工审批与安全恢复", "text": "可以拒绝计划。"},
                {"id": "ev_2", "title": "LangGraph 状态", "text": "Checkpoint 存储。"},
                {"id": "ev_3", "title": "园艺", "text": "修剪枝叶。"}]
    kept, rejected = filter_evidence("比较 LangGraph 的持久化与人工审批方案", evidence)
    assert [item["id"] for item in kept] == ["ev_1", "ev_2"]
    assert [item["id"] for item in rejected] == ["ev_3"]


def test_relevance_has_no_game_specific_knowledge():
    evidence = [{"id": "ev_a", "title": "橡树种植", "text": "橡树需要充足空间。"},
                {"id": "ev_b", "title": "模型工具", "text": "工具返回的数据仅供参考。"}]
    kept, _ = filter_evidence("如何种植橡树？", evidence)
    assert [item["id"] for item in kept] == ["ev_a"]


def test_generic_mapping_category_cannot_admit_unrelated_evidence(cultural_evidence):
    unrelated = {"id": "ev_tech", "title": "研究关系与来源验证",
                 "text": "原型模型研究：比较国家数据搜索来源的 Agent 技术方案。"}
    kept, rejected = filter_evidence("搜索原神中的国家与现实国家的对应关系", cultural_evidence + [unrelated])
    assert kept == cultural_evidence
    assert [e["id"] for e in rejected] == ["ev_tech"]


def test_relevance_handles_english_inflections_without_substring_matches():
    evidence = [{"id": "ev_state", "title": "LangGraph persistence", "text": "Checkpoint saves state."},
                {"id": "ev_food", "title": "Meals", "text": "A checkpointed ingredient list."}]
    kept, rejected = filter_evidence("Research persistent checkpoints", evidence)
    assert [e["id"] for e in kept] == ["ev_state"]
    assert [e["id"] for e in rejected] == ["ev_food"]


def test_mapping_pairs_preserve_supplied_values_without_inventing(cultural_evidence):
    pairs = mapping_pairs(cultural_evidence)
    assert [pair["item"] for pair in pairs] == ["蒙德", "璃月", "稻妻"]
    assert pairs[0] == {"item": "蒙德", "value": "德国（资料中的未核验说法）", "evidence_id": "ev_cultures"}


def test_mapping_pairs_handle_markdown_and_ignore_metadata():
    evidence = [{"id": "ev_a", "text": "来源: 用户\nhttps://example.org\n# 对应:参考\n"
                 "| 类型 | 含义 |\n| --- | --- |\n| 200 | 成功 |\n| 404 | 未找到 |\n\n"
                 "- **500**：服务错误"}]
    assert [pair["item"] for pair in mapping_pairs(evidence)] == ["200", "404", "500"]


def test_explicit_subset_does_not_require_every_item_in_document(cultural_evidence):
    contract = answer_contract("仅说明蒙德和璃月与现实国家的对应关系", cultural_evidence)
    assert contract["required_items"] == ["蒙德", "璃月"]
    assert "未核验" in contract["instructions"]


def test_all_items_still_required_when_question_mentions_an_example(cultural_evidence):
    contract = answer_contract("列出所有原神国家（比如蒙德）的对应关系", cultural_evidence)
    assert contract["required_items"] == ["蒙德", "璃月", "稻妻"]


def test_short_latin_items_do_not_match_inside_other_words():
    evidence = [{"id": "ev_1", "text": "R: Red\nB: Blue\nG: Green"}]
    contract = answer_contract("Map colors to meanings", evidence)
    assert contract["required_items"] == ["R", "B", "G"]
    selected = answer_contract("Map B to its meaning", evidence)
    assert selected["required_items"] == ["B"]


def test_complete_unverified_mapping_passes(cultural_evidence):
    question = "搜索原神中的国家与现实国家的对应关系"
    report = ("# 资料中的对应关系\n\n以下关系未独立核验。\n\n"
              "| 对象 | 对应地区 | 依据 |\n| --- | --- | --- |\n"
              "| 蒙德 | 德国 | [ev_cultures] |\n"
              "| 璃月 | 中国 | [ev_cultures] |\n"
              "| 稻妻 | 日本 | [ev_cultures] |")
    result = check_answer(question, report, cultural_evidence, answer_contract(question, cultural_evidence))
    assert result["passed"]
    assert result["covered_items"] == ["蒙德", "璃月", "稻妻"]


def test_citations_or_source_names_do_not_substitute_for_answer(cultural_evidence):
    question = "原神国家对应关系"
    report = ("# 拒答\n\n不能给出官方确认的结论。[ev_cultures]\n\n"
              "## 证据来源\n\n蒙德、璃月、稻妻的资料 [ev_cultures]")
    result = check_answer(question, report, cultural_evidence, answer_contract(question, cultural_evidence))
    assert not result["passed"]
    assert result["missing_items"] == ["蒙德", "璃月", "稻妻"]


def test_unverified_status_alone_is_not_a_mapping_answer(cultural_evidence):
    question = "蒙德对应关系"
    report = "| 对象 | 对应地区 |\n| --- | --- |\n| 蒙德 | 未核验 |"
    result = check_answer(question, report, cultural_evidence, answer_contract(question, cultural_evidence))
    assert not result["completeness_passed"]
    assert result["missing_items"] == ["蒙德"]


@pytest.mark.parametrize("report", [
    "# 结论\n\n在所给证据中有一条片段，它以概括",
    "# 结论\n\n例如：",
    "# 结论\n\n```python\nprint('hello')",
    "# 结论\n\n正文。\n\n## 未完成章节",
    "| 对象 | 含义 | 依据 |\n| --- | --- | --- |\n| A | 含义 |",
])
def test_obvious_truncation_is_a_hard_failure(report):
    result = check_answer("说明", report, [{"id": "ev_a"}], {"output_format": "direct_answer"})
    assert not result["passed"]
    assert not result["format_passed"]


def test_no_evidence_is_insufficient_not_complete():
    question = "找出知识库中不存在的事实"
    result = check_answer(question, "没有相关证据，无法回答。", [], answer_contract(question, []))
    assert not result["completeness_passed"]


def test_qualified_answer_is_not_mistaken_for_total_refusal():
    report = "无法确认这是官方说明。\n\n资料称持久化检查点可以恢复任务。[ev_a]"
    result = check_answer("解释持久化", report, [{"id": "ev_a"}], {"output_format": "explanation"})
    assert result["passed"]


def test_explanation_does_not_require_a_table_or_decision_sections():
    report = "Checkpoint state can be restored. [ev_a]"
    result = check_answer("Explain checkpoints", report, [{"id": "ev_a"}],
                          {"output_format": "explanation"})
    assert result["passed"]


def test_howto_requires_actual_numbered_steps():
    contract = answer_contract("如何运行项目？", [{"id": "ev_a"}])
    bad = check_answer("如何运行项目？", "可以执行运行命令。", [{"id": "ev_a"}], contract)
    good = check_answer("如何运行项目？", "1. 安装依赖。\n2. 启动服务。", [{"id": "ev_a"}], contract)
    assert not bad["passed"]
    assert good["passed"]
