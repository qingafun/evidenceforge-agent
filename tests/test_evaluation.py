import json

import pytest

from evidenceforge.evaluation import _metrics, evaluate


def test_metrics_deduplicate_documents_before_cutoff():
    chunks = [
        {"document_id": "irrelevant", "title": "Irrelevant"},
        {"document_id": "irrelevant", "title": "Irrelevant"},
        {"document_id": "relevant-a", "title": "Relevant A"},
        {"document_id": "irrelevant-2", "title": "Other"},
        {"document_id": "relevant-b", "title": "Relevant B"},
        {"document_id": "irrelevant-3", "title": "More"},
        {"document_id": "relevant-c", "title": "Relevant C"},
    ]
    metrics = _metrics(["Relevant A", "Relevant B", "Relevant C"], chunks)
    assert metrics["recall_at_5"] == pytest.approx(2 / 3, abs=1e-6)
    assert metrics["mrr"] == 0.5
    assert len(metrics["retrieved_titles"]) == 5
    assert "Relevant C" not in metrics["retrieved_titles"]


def test_zero_retrieval_scores_zero_without_fabricated_hits():
    assert _metrics(["Relevant"], []) == {"recall_at_5": 0.0, "mrr": 0.0, "retrieved_titles": []}
    with pytest.raises(ValueError, match="relevant"):
        _metrics([], [])


def test_bundled_evaluation_is_reproducible_and_writes_actual_results(tmp_path):
    output = tmp_path / "result.json"
    first = evaluate(output)
    assert json.loads(output.read_text(encoding="utf-8")) == first
    assert first["corpus_documents"] == 10
    assert first["summary"]["cases"] == 20
    assert len(first["cases"]) == 20
    for case in first["cases"]:
        assert case["expected_titles"]
        for mode in ("bm25", "hybrid"):
            assert 0 <= case[mode]["recall_at_5"] <= 1
            assert 0 <= case[mode]["mrr"] <= 1
    second = evaluate(tmp_path / "second.json")
    assert first["cases"] == second["cases"]
    assert first["summary"] == second["summary"]
    assert "not an independent or general benchmark" in first["notes"][0]
