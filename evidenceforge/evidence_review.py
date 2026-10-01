"""Versioned evidence reviews, distinct from independent fact verification."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .providers import ModelError


def cited_ids(state: dict) -> set[str]:
    report = state.get("report_body", state.get("report", ""))
    # Legacy reports contain an appended bibliography, which is not a claim.
    body = re.split(r"^##\s+证据来源\s*$", str(report), maxsplit=1, flags=re.MULTILINE)[0]
    return set(re.findall(r"\[([A-Za-z0-9_-]+)\]", body))


def fingerprint(question: str, state: dict, item: dict) -> str:
    """Tie a decision to the exact question, report body, and source excerpt."""
    value = {"question": question, "report": state.get("report_body", state.get("report", "")),
             "evidence": {key: item.get(key, "") for key in ("id", "title", "text", "source")}}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def present_reviews(question: str, state: dict) -> dict:
    """Old or changed material stays pending; never infer approval from run status."""
    saved = state.get("evidence_reviews") or {}
    result = {}
    for item in state.get("evidence", []):
        identifier = item["id"]
        version = fingerprint(question, state, item)
        previous = saved.get(identifier) or {}
        current = previous if previous.get("fingerprint") == version else {}
        result[identifier] = {"fingerprint": version,
                              "automatic": current.get("automatic"), "human": current.get("human")}
    return result


def human_review_blocked(question: str, state: dict) -> bool:
    used = cited_ids(state)
    return any(identifier in used and (review.get("human") or {}).get("verdict") in {"uncertain", "contradicted"}
               for identifier, review in present_reviews(question, state).items())


class _EvidenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    evidence_id: str = Field(min_length=1, max_length=200)
    verdict: Literal["supported", "uncertain", "contradicted", "not_used"]
    relevant: bool
    reason: str = Field(min_length=1, max_length=2000)


class _ReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviews: list[_EvidenceDecision] = Field(max_length=18)


def agent_reviews(question: str, state: dict, payload: dict, model: str) -> dict:
    """All entries must have a real, schema-checked decision before persisting."""
    try:
        decisions = _ReviewResponse.model_validate(payload).reviews
    except ValidationError:
        raise ModelError("证据审阅返回的格式不合法，未将任何条目标记为已审阅。") from None
    expected = {item["id"] for item in state.get("evidence", [])}
    received = [item.evidence_id for item in decisions]
    if len(received) != len(set(received)) or set(received) != expected:
        raise ModelError("证据审阅必须逐条覆盖当前证据，不能遗漏、重复或引用未知条目。")
    used = cited_ids(state)
    if any(item.evidence_id in used and item.verdict == "not_used" for item in decisions):
        raise ModelError("证据审阅把报告已引用的资料标记为未使用，请重新审阅。")
    results = present_reviews(question, state)
    stamp = datetime.now(timezone.utc).isoformat()
    for decision in decisions:
        results[decision.evidence_id]["automatic"] = {
            "method": "agent", "verdict": decision.verdict, "relevant": decision.relevant,
            "reason": decision.reason, "reviewed_at": stamp, "model": model,
        }
    return results


def rule_reviews(question: str, state: dict) -> dict:
    """Offline mode checks structure only; a person can supply semantic review."""
    results = present_reviews(question, state)
    used = cited_ids(state)
    stamp = datetime.now(timezone.utc).isoformat()
    for item in state.get("evidence", []):
        referenced = item["id"] in used
        has_text = bool(str(item.get("text", "")).strip())
        results[item["id"]]["automatic"] = {
            "method": "rules", "verdict": "unchecked" if referenced else "not_used", "relevant": None,
            "reason": ("已检查原文片段存在、引用编号可对应；离线模式没有调用模型，内容能否支持结论仍需人工审阅。"
                       if referenced and has_text else "原文内容为空，需要补充资料。" if referenced
                       else "该条目未被当前报告引用；未进行语义审阅。"),
            "reviewed_at": stamp,
        }
    return results


def review_gate(question: str, state: dict) -> dict:
    results = present_reviews(question, state)
    issues = []
    used = cited_ids(state)
    live = state.get("mode") == "live"
    if results and not used.intersection(results):
        issues.append("报告正文缺少有效证据引用，文末来源列表不能替代逐项引用。")
    for identifier, review in results.items():
        automatic = review.get("automatic") or {}
        expected_method = "agent" if live else "rules"
        if automatic.get("method") != expected_method or not automatic.get("reason"):
            issues.append(f"证据 {identifier} 尚未完成本次报告的审阅。")
        elif live and identifier in used and (automatic.get("verdict") != "supported"
                                              or automatic.get("relevant") is not True):
            issues.append(f"证据 {identifier} 不能充分支持当前引用：{automatic['reason']}")
    passed = bool(results) and not issues
    if not results:
        issues.append("没有可审阅的相关证据。")
    return {"evidence_review_passed": passed, "issues": issues}


def review_summary(question: str, state: dict) -> dict:
    results = present_reviews(question, state)
    agents = humans = rules = pending = attention = 0
    for review in results.values():
        human, automatic = review.get("human"), review.get("automatic")
        current = human or automatic or {}
        if human:
            humans += 1
        elif automatic and automatic.get("method") == "agent":
            agents += 1
        elif automatic and automatic.get("method") == "rules":
            rules += 1
        else:
            pending += 1
        attention += current.get("verdict") in {"uncertain", "contradicted"}
    return {"total": len(results), "agent": agents, "human": humans, "rules": rules,
            "pending": pending, "needs_attention": attention}


def export_review_appendix(question: str, state: dict) -> str:
    results = present_reviews(question, state)
    if not results:
        return ""
    labels = {"supported": "支持引用", "uncertain": "有疑问", "contradicted": "不支持引用",
              "not_used": "未用于报告", "unchecked": "待语义审阅"}

    def safe(value):
        text = re.sub(r"\s+", " ", str(value)).strip()
        return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", text)

    lines = ["", "", "## 证据审阅记录", "",
             "审阅检查资料与当前报告的支持关系，不等于独立事实核验或官方确认。", "",
             "| 证据 | 审阅方式 | 结论 | 理由 | 时间 |", "| --- | --- | --- | --- | --- |"]
    for identifier, review in results.items():
        decisions = [review[key] for key in ("automatic", "human") if review.get(key)]
        if not decisions:
            lines.append(f"| {safe(identifier)} | 待审阅 | — | 尚无审阅记录 | — |")
        for decision in decisions:
            method = {"agent": "Agent 已审阅", "human": "人工已审阅", "rules": "规则已检查"}.get(decision["method"], "待审阅")
            cells = [identifier, method, labels.get(decision["verdict"], decision["verdict"]),
                     decision["reason"], decision["reviewed_at"]]
            lines.append("| " + " | ".join(safe(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"
