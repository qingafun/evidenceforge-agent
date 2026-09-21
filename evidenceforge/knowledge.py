"""Small, inspectable bilingual retrieval without an API key or model download.

The two retrievers are BM25 and **sparse lexical** character n-gram TF-IDF.
Reciprocal rank fusion (RRF) combines their ranks. This is deliberately not a
neural embedding model: synonyms with no shared spelling may not be retrieved.
Original documents, chunk text, and Python character offsets are preserved.
The in-process ranking implementation is intended for a local knowledge base,
not a replacement for a search engine at millions-of-documents scale.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

CHUNK_SIZE = 900
CHUNK_OVERLAP = 140
_ENGLISH = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can do for from how i in is it of on or that the "
    "this to was we what when where which who why with you your about please".split()
)


def _tokens(text: str) -> list[str]:
    """English words plus overlapping Chinese words, with no external tokenizer."""
    result = [word for word in _ENGLISH.findall(text.lower()) if word not in _STOPWORDS]
    for run in _CJK.findall(text):
        if len(run) == 1:
            result.append(run)
        for width in (2, 3):
            result.extend(run[index : index + width] for index in range(len(run) - width + 1))
    return result


def _character_features(text: str) -> Counter[str]:
    features: Counter[str] = Counter()
    for word in _ENGLISH.findall(text.lower()):
        if word in _STOPWORDS:
            continue
        bounded = f"_{word}_"
        for width in (3, 4, 5):
            features.update(bounded[index : index + width] for index in range(len(bounded) - width + 1))
    for run in _CJK.findall(text):
        if len(run) == 1:
            features.update([run])
        for width in (2, 3):
            features.update(run[index : index + width] for index in range(len(run) - width + 1))
    return features


def _chunk_spans(content: str) -> Iterator[tuple[int, int]]:
    """Prefer paragraph boundaries, overlap context, never rewrite evidence."""
    start = 0
    while start < len(content):
        end = min(start + CHUNK_SIZE, len(content))
        if end < len(content):
            lower = start + CHUNK_SIZE // 2
            for delimiter in ("\n\n", "\n", "。", ". ", "; "):
                boundary = content.rfind(delimiter, lower, end)
                if boundary >= lower:
                    end = boundary + len(delimiter)
                    break
        if content[start:end].strip():
            yield start, end
        if end == len(content):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)


def _identifier(prefix: str, *parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return prefix + hashlib.sha256(payload).hexdigest()[:24]


class KnowledgeBase:
    """Thread-safe SQLite storage, with a fresh connection per operation.

    Duplicate identity is based on title, exact content and source. Different
    sources stay separate, even if they contain the same text. All timestamps
    use UTC; offsets count Unicode characters, not bytes or model tokens.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._memory_keeper: sqlite3.Connection | None = None
        self._uri = self.db_path == ":memory:"
        if self._uri:
            self.db_path = f"file:evidenceforge_kb_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._memory_keeper = sqlite3.connect(self.db_path, uri=True, check_same_thread=False)
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS kb_documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kb_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    start_char INTEGER NOT NULL,
                    end_char INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS kb_chunks_document ON kb_chunks(document_id);
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30, uri=self._uri)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add_document(self, title: str, content: str, source: str = "user") -> dict:
        title = title.strip()
        source = source.strip() or "user"
        if not title or not content.strip():
            raise ValueError("Document title and content must not be empty.")
        document_id = _identifier("doc_", title, content, source)
        spans = list(_chunk_spans(content))
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO kb_documents VALUES (?, ?, ?, ?, ?, ?)",
                (document_id, title, content, source, len(spans), created_at),
            ).rowcount
            if inserted:
                connection.executemany(
                    "INSERT INTO kb_chunks VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            _identifier("ev_", document_id, str(start), str(end)),
                            document_id,
                            content[start:end],
                            start,
                            end,
                        )
                        for start, end in spans
                    ],
                )
        return {"id": document_id, "title": title, "source": source, "chunk_count": len(spans)}

    def list_documents(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, title, source, chunk_count, created_at FROM kb_documents "
                "ORDER BY created_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_document(self, document_id: str) -> bool:
        with self._connection() as connection:
            return bool(connection.execute("DELETE FROM kb_documents WHERE id = ?", (document_id,)).rowcount)

    def stats(self) -> dict:
        with self._connection() as connection:
            return {
                "documents": connection.execute("SELECT COUNT(*) FROM kb_documents").fetchone()[0],
                "chunks": connection.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0],
            }

    def seed(self, directory: Path) -> int:
        """Import local Markdown fixtures once; changed files become new versions.

        Sources use portable relative fixture paths, so moving a checkout does
        not duplicate documents. This method does not download source URLs.
        """
        directory = Path(directory)
        added = 0
        for path in sorted(directory.rglob("*.md")):
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                continue
            heading = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
            title = heading.group(1).strip() if heading else path.stem
            source = "demo:" + path.relative_to(directory).as_posix()
            expected_id = _identifier("doc_", title, content, source)
            with self._connection() as connection:
                exists = connection.execute("SELECT 1 FROM kb_documents WHERE id = ?", (expected_id,)).fetchone()
            if not exists:
                self.add_document(title, content, source)
                added += 1
        return added

    @staticmethod
    def _select_chunks() -> str:
        return (
            "SELECT c.id, c.document_id, d.title, c.text, d.source, c.start_char, c.end_char "
            "FROM kb_chunks c JOIN kb_documents d ON c.document_id = d.id"
        )

    def read_chunk(self, chunk_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(self._select_chunks() + " WHERE c.id = ?", (chunk_id,)).fetchone()
        return dict(row) if row else None

    def search(self, query: str, limit: int = 6, mode: str = "hybrid") -> list[dict]:
        """Rank evidence; scores are ranking signals, never truth probabilities.

        Modes: ``bm25``, ``tfidf`` (sparse character cosine), ``hybrid`` (RRF).
        Empty/stopword-only queries return nothing. Character retrieval requires
        both minimum cosine and feature overlap to avoid arbitrary weak hits.
        Results expose exact text with exclusive ``end_char`` offsets.
        """
        if mode not in {"bm25", "tfidf", "hybrid"}:
            raise ValueError("Retrieval mode must be hybrid, bm25, or tfidf.")
        if limit <= 0 or not query.strip() or not _tokens(query):
            return []
        limit = min(int(limit), 50)
        with self._connection() as connection:
            rows = [dict(row) for row in connection.execute(self._select_chunks() + " ORDER BY c.id")]
        if not rows:
            return []
        indexed_text = [f"{row['title']}\n{row['title']}\n{row['text']}" for row in rows]
        rankings: list[dict[int, float]] = []
        if mode in {"bm25", "hybrid"}:
            rankings.append(self._bm25(query, indexed_text))
        if mode in {"tfidf", "hybrid"}:
            rankings.append(self._tfidf(query, indexed_text))
        if mode == "hybrid":
            scores: dict[int, float] = {}
            for ranking in rankings:
                for rank, index in enumerate(sorted(ranking, key=lambda item: (-ranking[item], rows[item]["id"])), 1):
                    scores[index] = scores.get(index, 0.0) + 1.0 / (60 + rank)
        else:
            scores = rankings[0]
        best = sorted(scores, key=lambda index: (-scores[index], rows[index]["id"]))[:limit]
        return [{**rows[index], "score": round(scores[index], 8)} for index in best]

    @staticmethod
    def _bm25(query: str, documents: list[str]) -> dict[int, float]:
        counters = [Counter(_tokens(document)) for document in documents]
        lengths = [sum(counter.values()) for counter in counters]
        average_length = sum(lengths) / len(lengths) or 1
        frequencies: Counter[str] = Counter()
        for counter in counters:
            frequencies.update(counter.keys())
        terms = set(_tokens(query))
        scores: dict[int, float] = {}
        for index, counter in enumerate(counters):
            score = 0.0
            for term in terms & counter.keys():
                frequency = counter[term]
                idf = math.log(1 + (len(counters) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * lengths[index] / average_length)
                score += idf * frequency * 2.5 / denominator
            if score > 0:
                scores[index] = score
        return scores

    @staticmethod
    def _tfidf(query: str, documents: list[str]) -> dict[int, float]:
        counters = [_character_features(document) for document in documents]
        frequencies: Counter[str] = Counter()
        for counter in counters:
            frequencies.update(counter.keys())
        query_counter = _character_features(query)
        if not query_counter:
            return {}

        def weight(term: str, count: int) -> float:
            return (1 + math.log(count)) * (1 + math.log((1 + len(counters)) / (1 + frequencies[term])))

        query_vector = {term: weight(term, count) for term, count in query_counter.items()}
        query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
        scores: dict[int, float] = {}
        for index, counter in enumerate(counters):
            common = counter.keys() & query_vector.keys()
            if len(common) / len(query_vector) < 0.18:
                continue
            vector = {term: weight(term, count) for term, count in counter.items()}
            norm = math.sqrt(sum(value * value for value in vector.values()))
            if not norm:
                continue
            score = sum(vector[term] * query_vector[term] for term in common) / (norm * query_norm)
            if score >= 0.04:
                scores[index] = score
        return scores
