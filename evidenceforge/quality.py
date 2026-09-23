"""Question contracts and conservative, deterministic answer-quality checks.

These checks enforce observable structure and coverage; they are not a semantic
judge or a source-authentication service. Live runs also need an independent
semantic review. A source's display label does not establish its provenance.
"""

from __future__ import annotations

import re


TASK_TYPES = frozenset({"mapping", "comparison", "howto", "decision", "explanation", "factual"})
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_WORDS = re.compile(r"[a-z][a-z0-9_+.-]*", re.IGNORECASE)
_ENGLISH_FILLER = frozenset(
    "a an and are as at be by can could do does for from how i in is it me of on or please "
    "search find research show tell that the their these this those to was we what when where "
    "which who why with would you your compare comparison explain explanation related "
    "correspond corresponding correspondence relationship relationships each all respectively ".split()
)
_CHINESE_FILLER = re.compile(
    "搜索|搜寻|寻找|检索|帮我|请问|请|分析|研究|介绍|解释|说明|比较|对比|如何|怎样|为什么|"
    "分别|来自于|来自|对应关系|对应|映射|相关关系|关系|哪些|那些|什么|各个|各自|所有|"
    "中的|里面的|有关|关于|以及|或者|还是|一个|一下|的|与|和|及|在|是|有|吗|呢"
)
_WEAK_TERMS = frozenset({
    "方案", "问题", "内容", "答案", "结果", "信息", "资料", "文档", "官方", "来源", "证据",
    "结论", "原型", "建议", "方法", "source", "sources", "official", "evidence", "information",
    "answer", "answers", "report", "reports", "document", "documents", "result", "results",
})
_PAIR_LABELS = frozenset({
    "来源", "出处", "说明", "注意", "备注", "结论", "研究问题", "问题", "参考", "参考资料",
    "参考文献", "证据", "限制", "建议", "source", "sources", "note", "notes", "question",
    "reference", "references", "title", "标题", "状态", "核验状态", "verification",
})
_EMPTY_VALUES = frozenset({
    "", "-", "—", "–", "未知", "暂无", "未提供", "未核验", "未验证", "待核验", "待补充", "未找到",
    "无法确认", "不能确认", "不确定", "unknown", "unverified", "n/a", "none", "tbd",
})
_CITATION = re.compile(r"\[[A-Za-z0-9_-]+\]")
_MAPPING_CONTEXT = re.compile(r"现实(?:世界)?|国家|地区|各国")


def classify_question(question: str) -> str:
    """Choose an answer shape from the request, without assuming a subject area."""
    lowered = question.casefold()
    if re.search(r"对应|映射|来自.*(?:国家|地区)|(?:国家|地区).*来自|文化原型|现实.*原型|"
                 r"correspond|\bmapping\b|\bmap\b.+\bto\b|inspired by|based on which", lowered):
        return "mapping"
    if re.search(r"比较|对比|区别|异同|优劣|\bcompare\b|\bcomparison\b|\bversus\b|\bvs\.?\b|"
                 r"\bdifference\b", lowered):
        return "comparison"
    if re.search(r"如何|怎样|怎么|步骤|教程|\bhow (?:to|do|can|should)\b|\btutorial\b|\bstep.by.step\b",
                 lowered):
        return "howto"
    if re.search(r"推荐|选型|选择.*(?:方案|工具|框架)|建议.*(?:方案|工具|框架)|\brecommend|"
                 r"\bwhich.+\b(?:choose|use)\b|\bdecision\b", lowered):
        return "decision"
    if re.search(r"为什么|原理|解释|阐释|\bwhy\b|\bexplain\b|\bhow does\b", lowered):
        return "explanation"
    return "factual"


def _english_stem(word: str) -> str:
    """Handle common inflections without treating arbitrary prefixes as synonyms."""
    word = word.casefold().strip(".-")
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    for suffix in ("ence", "ent"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 5:
            return word[:-len(suffix)]
    return word


def _topic_terms(question: str) -> set[str]:
    terms = {word.casefold().strip(".-") for word in _WORDS.findall(question)}
    terms -= _ENGLISH_FILLER | _WEAK_TERMS
    subject = _CHINESE_FILLER.sub(" ", question)
    if classify_question(question) == "mapping":
        # A mapping's target category is not enough to establish its subject:
        # "国家" alone must not admit arbitrary national datasets into a game question.
        subject = _MAPPING_CONTEXT.sub(" ", subject)
    for run in _CJK.findall(subject):
        if len(run) >= 2 and run not in _WEAK_TERMS:
            terms.add(run)
            # Longer unsegmented Chinese queries need n-grams to share wording
            # with relevant titles; common request/provenance terms carry no weight.
            if len(run) > 3:
                terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms - _WEAK_TERMS


def filter_evidence(question: str, evidence: list[dict]) -> tuple[list[dict], list[dict]]:
    """Reject candidates with no topical lexical support; preserve input order.

    This deliberately conservative pre-filter is followed by semantic selection
    in live mode. It does not use RRF scores as relevance probabilities or treat
    a citation's source label as evidence of relevance. Very broad/empty requests
    with no usable topical terms are left for semantic review.
    """
    terms = _topic_terms(question)
    english_terms = {_english_stem(term) for term in terms if re.fullmatch(r"[a-z][a-z0-9_+.-]*", term)}
    chinese_terms = terms - {term for term in terms if re.fullmatch(r"[a-z][a-z0-9_+.-]*", term)}
    kept, rejected = [], []
    for item in evidence:
        haystack = f"{item.get('title', '')}\n{item.get('text', '')}".casefold()
        words = {_english_stem(word) for word in _WORDS.findall(haystack)}
        if not terms or any(term in haystack for term in chinese_terms) or english_terms & words:
            kept.append(item)
        else:
            rejected.append({**item, "rejection_reason": "标题和正文均缺少研究问题的主题词。"})
    return kept, rejected


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _is_separator(cells: list[str]) -> bool:
    return len(cells) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _plain(value: str) -> str:
    return _CITATION.sub("", value).replace("**", "").replace("__", "").strip(" `")


def _mentions_item(item: str, text: str) -> bool:
    # Short Latin identifiers (A, R, C) must not match arbitrary word fragments.
    if re.fullmatch(r"[a-z0-9_+.-]+", item, re.IGNORECASE):
        return bool(re.search(r"(?<![a-z0-9_])" + re.escape(item) + r"(?![a-z0-9_])",
                              text, re.IGNORECASE))
    return item.casefold() in text.casefold()


def mapping_pairs(evidence: list[dict]) -> list[dict]:
    """Extract explicit ``item: value`` lines or Markdown table pairs verbatim.

    The parser infers no cultural or factual relationships. Values retain their
    original qualifiers and references; the caller must label them as supplied,
    unverified material. Duplicate claims from separate sources are preserved.
    """
    pairs: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in evidence:
        lines = str(entry.get("text", "")).splitlines()
        table = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                table = False
                continue
            cells = _cells(stripped) if "|" in stripped else []
            if _is_separator(cells):
                table = True
                continue
            if cells and index + 1 < len(lines) and _is_separator(_cells(lines[index + 1])):
                continue  # A table's header names are not answer entities.
            if table and len(cells) >= 2:
                item, value = cells[0], cells[1]
            else:
                table = False
                if stripped.startswith("#"):
                    continue
                candidate = re.sub(r"^(?:[-*+]\s+|\d+[.)、]\s*)", "", stripped)
                match = re.match(r"^([^:：\n]{1,60})[:：]\s*(.+)$", candidate)
                if not match:
                    continue
                item, value = match.groups()
            item, value = _plain(item), value.strip()
            if (not item or item.casefold() in _PAIR_LABELS or len(item) > 60
                    or re.search(r"[。！？!?；;]", item)
                    or item.casefold() in {"http", "https"} or not value):
                continue
            key = (item, value, str(entry.get("id", "")))
            if key not in seen:
                pairs.append({"item": item, "value": value, "evidence_id": key[2]})
                seen.add(key)
    return pairs


def answer_contract(question: str, evidence: list[dict], task_type: str | None = None) -> dict:
    """Describe the requested deliverable and coverage visible in available data."""
    kind = task_type if isinstance(task_type, str) and task_type in TASK_TYPES else classify_question(question)
    shapes = {
        "mapping": ("mapping_table", "用对应关系表直接回答：对象、对应对象或地区、依据与核验状态。"
                    "逐项列出已有材料中的关系；不要把文化参考强行写成一一等同。"),
        "comparison": ("comparison_table", "先给对比表，按问题要求的维度列出各对象的实际差异，再说明结论。"),
        "howto": ("numbered_steps", "用有序步骤回答操作方法，并提供必要前提和可检查的结果。"),
        "decision": ("decision_report", "用建议、依据与约束等分节说明决策，并交代适用条件。"),
        "explanation": ("explanation", "先直接解释所问概念或原因，再用依据和必要例子展开。"),
        "factual": ("direct_answer", "直接回答问题，必要时用清单组织事实，并交代证据边界。"),
    }
    output_format, instructions = shapes[kind]
    required_items: list[str] = []
    if kind == "mapping":
        required_items = list(dict.fromkeys(pair["item"] for pair in mapping_pairs(evidence)))
        named = [item for item in required_items if _mentions_item(item, question)]
        requests_all = re.search(r"所有|各个|各国|全部|分别|\ball\b|\beach\b", question, re.IGNORECASE)
        if named and not requests_all:
            required_items = named
    instructions += ("有相关材料但未能独立核验时，应列出材料支持的答案并明确标记未核验；"
                     "不能仅因未找到官方确认而拒绝回答。来源名称本身不能证明官方背书。"
                     "没有相关材料时才说明证据不足，且不得把未回答的报告标记为完成。")
    return {"task_type": kind, "output_format": output_format, "instructions": instructions,
            "required_items": required_items, "evidence_available": bool(evidence)}


def _tables(report: str) -> tuple[list[list[str]], bool]:
    """Return data rows from real Markdown tables and an unfinished-row flag."""
    lines = report.splitlines()
    rows: list[list[str]] = []
    malformed = False
    columns = 0
    for index, line in enumerate(lines):
        cells = _cells(line) if "|" in line else []
        if _is_separator(cells):
            header = _cells(lines[index - 1]) if index else []
            if len(header) == len(cells):
                columns = len(cells)
            else:
                malformed = True
            continue
        if columns and cells:
            if len(cells) != columns or any(not _plain(cell) for cell in cells[:2]):
                malformed = True
            rows.append(cells)
        else:
            columns = 0
    return rows, malformed


def _substantive_value(value: str) -> bool:
    cleaned = _plain(value).strip("。.;；()（） ").casefold()
    return cleaned not in _EMPTY_VALUES


def check_answer(question: str, report: str, evidence: list[dict], contract: dict) -> dict:
    """Gate success on answer shape, observed coverage and obvious truncation.

    Passing this gate is necessary, not sufficient: it cannot prove semantic
    correctness, detect every truncation, or authenticate an external source.
    Citation validation, provider finish reasons and live semantic review are
    enforced separately by the workflow.
    """
    del question  # All extracted requirements are frozen in the persisted contract.
    issues: list[str] = []
    structural: list[str] = []
    # A bibliography is not an answer and cannot satisfy required-item coverage.
    body = re.split(r"^#{1,6}\s+(?:证据来源|参考文献|参考资料|Sources|References)\s*$",
                    report, maxsplit=1, flags=re.MULTILINE | re.IGNORECASE)[0].strip()
    prose = re.sub(r"^\s*#{1,6}[^\n]*$", "", body, flags=re.MULTILINE).strip()
    if not evidence:
        issues.append("没有相关证据，当前结果是证据不足，不能作为完整答案通过。")
    if not _plain(prose):
        issues.append("报告没有实际回答内容。")
    fences = re.findall(r"^\s*(`{3,}|~{3,})", body, flags=re.MULTILINE)
    if len(fences) % 2:
        structural.append("报告存在未闭合的代码块，可能被截断。")
    rows, malformed = _tables(body)
    if malformed:
        structural.append("对应或比较表存在缺失单元格，可能被截断。")
    last_line = body.splitlines()[-1] if body else ""
    if re.match(r"^\s*#{1,6}\s+", last_line):
        structural.append("报告以没有正文的标题结束。")
    tail = _plain(body).rstrip()
    if re.search(r"(?:[，、：:,（(]|以及|包括|例如|它以概括|分别为|对应于|如下)\s*$", tail):
        structural.append("报告在未完成的句子或引导语处结束，可能被截断。")
    shape = contract.get("output_format")
    if shape in {"mapping_table", "comparison_table"} and not rows:
        structural.append("问题要求逐项对应或比较，但报告没有有效的数据表。")
    if shape == "numbered_steps" and not re.search(r"^\s*\d+[.)、]\s*\S", body, re.MULTILINE):
        structural.append("操作问题的报告缺少有序步骤。")
    if shape == "decision_report" and len(re.findall(r"^#{2,6}\s+\S", body, re.MULTILINE)) < 2:
        structural.append("决策报告缺少分别说明建议和依据的章节。")

    required = list(contract.get("required_items", []))
    covered = []
    for item in required:
        for row in rows:
            if len(row) >= 2 and _mentions_item(item, _plain(row[0])) and _substantive_value(row[1]):
                covered.append(item)
                break
    missing = [item for item in required if item not in covered]
    if missing:
        issues.append("答案缺少以下对象的实际对应内容：" + "、".join(missing))
    if shape == "mapping_table" and rows and not any(
            len(row) >= 2 and _substantive_value(row[1]) for row in rows):
        issues.append("对应表只有未知或核验状态，没有实际对应关系。")

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", prose) if part.strip()]
    refusal = re.compile(r"(?:无法|不能|不应|不要|不宜).{0,16}(?:回答|输出|给出|提供)|"
                         r"明确弃权|(?:cannot|can.t|unable to) (?:answer|provide)|\babstain", re.IGNORECASE)
    if evidence and paragraphs and not rows and all(refusal.search(part) for part in paragraphs):
        issues.append("已有相关材料，但报告只有拒答；应区分未核验的信息与无证据可回答。")
    issues.extend(structural)
    return {"passed": not issues, "completeness_passed": not issues, "format_passed": not structural,
            "issues": issues, "required_items": required, "covered_items": covered, "missing_items": missing}
