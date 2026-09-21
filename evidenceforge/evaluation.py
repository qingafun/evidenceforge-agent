"""Reproducible retrieval smoke evaluation; no models, API calls, or paid usage."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from .knowledge import KnowledgeBase


def _metrics(expected_titles: list[str], ranked_chunks: list[dict]) -> dict:
    """Deduplicate documents, then measure Recall@5 and reciprocal rank @5."""
    if not expected_titles:
        raise ValueError("Each evaluation case must have at least one relevant title.")
    seen_documents: set[str] = set()
    ranked_titles: list[str] = []
    for chunk in ranked_chunks:
        if chunk["document_id"] in seen_documents:
            continue
        seen_documents.add(chunk["document_id"])
        ranked_titles.append(chunk["title"])
        if len(ranked_titles) == 5:
            break
    expected = set(expected_titles)
    matched = expected.intersection(ranked_titles)
    reciprocal_rank = next(
        (1 / rank for rank, title in enumerate(ranked_titles, 1) if title in expected), 0.0
    )
    return {
        "recall_at_5": round(len(matched) / len(expected), 6),
        "mrr": round(reciprocal_rank, 6),
        "retrieved_titles": ranked_titles,
    }


def _load_cases() -> list[dict]:
    fixture = files("evals").joinpath("retrieval.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    cases = payload["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Evaluation case IDs must be nonempty and unique.")
    return cases


def evaluate(output: Path) -> dict:
    """Evaluate a clean, bundled corpus and write the exact returned JSON report.

    The user knowledge database is never read or changed. Repeated runs differ
    only in the generated_at timestamp while corpus and fixture versions match.
    ``mrr`` is MRR@5 over distinct retrieved documents, not unrestricted MRR.
    """
    corpus = Path(__file__).resolve().parent / "corpus"
    if not corpus.is_dir():
        corpus = Path(__file__).resolve().parents[1] / "examples" / "knowledge"
    cases = _load_cases()
    with tempfile.TemporaryDirectory(prefix="evidenceforge-eval-") as directory:
        knowledge = KnowledgeBase(Path(directory) / "isolated-knowledge.sqlite3")
        knowledge.seed(corpus)
        documents = knowledge.list_documents()
        available_titles = {document["title"] for document in documents}
        evaluated = []
        for case in cases:
            missing = set(case["expected_titles"]) - available_titles
            if missing:
                raise ValueError(f"Evaluation {case['id']} references missing titles: {sorted(missing)}")
            scores = {
                mode: _metrics(case["expected_titles"], knowledge.search(case["query"], limit=50, mode=mode))
                for mode in ("bm25", "hybrid")
            }
            evaluated.append({**case, **scores})
        summary = {"cases": len(evaluated)}
        for mode in ("bm25", "hybrid"):
            summary[mode] = {
                metric: round(sum(case[mode][metric] for case in evaluated) / len(evaluated), 6)
                for metric in ("recall_at_5", "mrr")
            }
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "corpus_documents": len(documents),
            "cases": evaluated,
            "summary": summary,
            "notes": [
                "Small authored smoke benchmark over bundled demo notes, not an independent or general benchmark.",
                "Cases and labels were authored before scoring; no model calls, API keys, network or paid usage.",
                "Ranking requests up to 50 chunks, deduplicates document IDs, then takes the first 5 documents.",
                "recall_at_5 = relevant documents retrieved / all labeled relevant documents; mrr = MRR@5.",
                "BM25 and character n-gram TF-IDF are both sparse lexical methods; hybrid uses RRF, not neural embeddings.",
                "Results measure retrieval only, not answer correctness, citation support or real-model reasoning.",
                "A stronger result on these fixtures alone is not evidence of broader quality gains.",
            ],
        }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
