"""One-time demo corpus bootstrap shared by the web app and MCP server."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .knowledge import KnowledgeBase


def seed_demo_once(knowledge: KnowledgeBase, directory: Path) -> int:
    """Import initial examples without resurrecting deliberately deleted data.

    Completion is stored separately from documents in the same SQLite database.
    A failed import leaves no completion marker and can be retried; seed IDs are
    content-based, so a retry or simultaneous first startup does not duplicate
    documents. Existing demo sources migrate older databases without reseeding.
    """
    with closing(sqlite3.connect(knowledge.db_path, timeout=30,
                                 uri=knowledge.db_path.startswith("file:"))) as connection:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS ef_bootstrap ("
                               "name TEXT PRIMARY KEY, completed_at TEXT NOT NULL)")
        if connection.execute("SELECT 1 FROM ef_bootstrap WHERE name='demo_corpus'").fetchone():
            return 0

        started = connection.execute("SELECT 1 FROM ef_bootstrap WHERE name='demo_corpus_started'").fetchone()
        existing_demo = any(item["source"].startswith("demo:") for item in knowledge.list_documents())
        added = 0
        if started or not existing_demo:
            # Do not mark a missing package resource as a successful import.
            directory = Path(directory)
            if not directory.is_dir():
                return 0
            with connection:
                connection.execute("INSERT OR IGNORE INTO ef_bootstrap VALUES (?, ?)",
                                   ("demo_corpus_started", datetime.now(timezone.utc).isoformat()))
            added = knowledge.seed(directory)
            if not any(item["source"].startswith("demo:") for item in knowledge.list_documents()):
                return 0

        with connection:
            connection.execute("INSERT OR IGNORE INTO ef_bootstrap VALUES (?, ?)",
                               ("demo_corpus", datetime.now(timezone.utc).isoformat()))
            connection.execute("DELETE FROM ef_bootstrap WHERE name='demo_corpus_started'")
        return added
