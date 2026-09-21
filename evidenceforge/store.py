"""SQLite application records; graph checkpoints use a separate database."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
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

    def update_run(self, run_id: str, *, status: str | None = None,
                   state: dict | None = None, error: str | None = None) -> dict:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._run(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            if record is None:
                raise KeyError(run_id)
            record["state"].update(state or {})
            # A cancellation cannot be overwritten by a worker finishing a node.
            new_status = "cancelled" if record["status"] == "cancelled" else (status or record["status"])
            current_error = error if error is not None else record["error"]
            if status in ("running", "completed", "awaiting_approval"):
                current_error = None
            conn.execute("UPDATE runs SET status=?,state=?,error=?,updated_at=? WHERE id=?",
                         (new_status, json.dumps(record["state"], ensure_ascii=False), current_error, now(), run_id))
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
            conn.execute("INSERT INTO events(run_id,node,kind,message,data,created_at) VALUES(?,?,?,?,?,?)",
                         (run_id, node, kind, message, json.dumps(data or {}, ensure_ascii=False), now()))

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
