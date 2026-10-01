"""SQLite application records; graph checkpoints use a separate database."""

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .evidence_review import fingerprint, present_reviews


class EvidenceReviewError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class RunDeletionError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._execution_lock = threading.RLock()
        self._executing: set[str] = set()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL, mode TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    state TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    node TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id,id);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _run(row):
        if row is None:
            return None
        record = dict(row)
        record["state"] = json.loads(record["state"])
        return record

    def create_run(self, question: str, mode: str, options: dict) -> dict:
        run_id = uuid.uuid4().hex
        stamp = now()
        state = {"run_id": run_id, "question": question, "mode": mode, **options}
        with self.connection() as conn:
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,NULL)",
                         (run_id, question, mode, "queued", stamp, stamp, json.dumps(state)))
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict | None:
        with self.connection() as conn:
            return self._run(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self) -> list[dict]:
        with self.connection() as conn:
            return [self._run(row) for row in conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT 100")]

    @contextmanager
    def execution(self, run_id: str):
        """Keep cancelled/finished workers protected until their final writes end."""
        with self._execution_lock:
            acquired = run_id not in self._executing
            if acquired:
                self._executing.add(run_id)
        try:
            yield acquired
        finally:
            if acquired:
                with self._execution_lock:
                    self._executing.remove(run_id)

    def delete_run(self, run_id: str, checkpoint_path: Path | str):
        """Remove one stopped task and its trace/checkpoints, keeping shared data."""
        with self._execution_lock, self.connection() as conn:
            if run_id in self._executing:
                raise RunDeletionError(409, "任务仍在结束处理中，请稍后再删除。")
            checkpoint_path = Path(checkpoint_path)
            has_checkpoints = checkpoint_path.is_file()
            if has_checkpoints:
                conn.execute("ATTACH DATABASE ? AS run_checkpoints", (str(checkpoint_path),))
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise RunDeletionError(404, "研究记录不存在或已删除。")
            if row["status"] not in {"completed", "failed", "cancelled"}:
                raise RunDeletionError(409, "请先停止研究，再删除历史记录。")
            if has_checkpoints:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM run_checkpoints.sqlite_master WHERE type='table'")}
                # These are the two task tables in the pinned SQLite checkpointer.
                for table in ("writes", "checkpoints"):
                    if table in tables:
                        conn.execute(f"DELETE FROM run_checkpoints.{table} WHERE thread_id=?", (run_id,))
            conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM runs WHERE id=?", (run_id,))

    def update_run(self, run_id: str, *, status: str | None = None,
                   state: dict | None = None, error: str | None = None) -> dict:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._run(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            if record is None:
                raise KeyError(run_id)
            previous_reviews = present_reviews(record["question"], record["state"])
            record["state"].update(state or {})
            # A cancelled worker may return after a person reviewed its draft.
            # Retain those decisions only while their exact material is unchanged.
            current_reviews = present_reviews(record["question"], record["state"])
            for identifier, previous in previous_reviews.items():
                current = current_reviews.get(identifier)
                if current and previous["fingerprint"] == current["fingerprint"] and previous.get("human"):
                    current["human"] = previous["human"]
            if current_reviews:
                record["state"]["evidence_reviews"] = current_reviews
            # A cancellation cannot be overwritten by a worker finishing a node.
            new_status = "cancelled" if record["status"] == "cancelled" else (status or record["status"])
            current_error = error if error is not None else record["error"]
            if status in ("running", "completed", "awaiting_approval"):
                current_error = None
            conn.execute("UPDATE runs SET status=?,state=?,error=?,updated_at=? WHERE id=?",
                         (new_status, json.dumps(record["state"], ensure_ascii=False), current_error, now(), run_id))
        return self.get_run(run_id)

    def review_evidence(self, run_id: str, evidence_id: str, version: str,
                        verdict: str, reason: str) -> dict:
        """Commit one human judgment and its audit event against current material."""
        if verdict not in {"supported", "uncertain", "contradicted"} or not 1 <= len(reason.strip()) <= 2000:
            raise EvidenceReviewError(422, "审阅结论或理由不合法。")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._run(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            if run is None:
                raise EvidenceReviewError(404, "任务不存在")
            state = run["state"]
            item = next((item for item in state.get("evidence", []) if item["id"] == evidence_id), None)
            if item is None:
                raise EvidenceReviewError(404, "证据不存在")
            if run["status"] not in {"completed", "failed", "cancelled"} or not state.get("report"):
                raise EvidenceReviewError(409, "请在任务结束并生成报告后审阅证据。")
            current_version = fingerprint(run["question"], state, item)
            if version != current_version:
                raise EvidenceReviewError(409, "报告或证据已变化，请刷新后重新审阅。")
            reviews = present_reviews(run["question"], state)
            previous = reviews[evidence_id].get("human")
            stamp = now()
            human = {"method": "human", "verdict": verdict, "reason": reason.strip(), "reviewed_at": stamp}
            reviews[evidence_id]["human"] = human
            state["evidence_reviews"] = reviews
            conn.execute("UPDATE runs SET state=?,updated_at=? WHERE id=?",
                         (json.dumps(state, ensure_ascii=False), stamp, run_id))
            audit = {"evidence_id": evidence_id, "fingerprint": current_version,
                     "previous": previous, "review": human}
            conn.execute("INSERT INTO events(run_id,node,kind,message,data,created_at) VALUES(?,?,?,?,?,?)",
                         (run_id, "evidence_review", "human_review", "已保存人工证据审阅。",
                          json.dumps(audit, ensure_ascii=False), stamp))
        return self.get_run(run_id)

    def transition(self, run_id: str, expected: tuple[str, ...], status: str) -> bool:
        with self.connection() as conn:
            placeholders = ",".join("?" for _ in expected)
            result = conn.execute(
                f"UPDATE runs SET status=?,updated_at=?,error=NULL WHERE id=? AND status IN ({placeholders})",
                (status, now(), run_id, *expected))
            return result.rowcount == 1

    def add_event(self, run_id: str, node: str, kind: str, message: str, data: dict | None = None):
        with self.connection() as conn:
            conn.execute("INSERT INTO events(run_id,node,kind,message,data,created_at) "
                         "SELECT ?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM runs WHERE id=?)",
                         (run_id, node, kind, message, json.dumps(data or {}, ensure_ascii=False), now(), run_id))

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 500",
                                (run_id, after)).fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["data"] = json.loads(record["data"])
            result.append(record)
        return result

    def list_memories(self) -> list[dict]:
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM memories ORDER BY created_at DESC LIMIT 100")]

    def add_memory(self, content: str) -> dict:
        with self.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO memories VALUES(?,?,?)", (uuid.uuid4().hex, content, now()))
            return dict(conn.execute("SELECT * FROM memories WHERE content=?", (content,)).fetchone())

    def delete_memory(self, memory_id: str) -> bool:
        with self.connection() as conn:
            return conn.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    def recover_interrupted(self):
        with self.connection() as conn:
            conn.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE status IN ('running','queued')",
                         ("服务重启使任务中断；可以从已保存的检查点恢复。", now()))
