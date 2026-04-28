"""Repository for the memory_items table — structured long-term learning across runs.

Memory is not free-form chat history. It stores typed, keyed entries with a
confidence score. The orchestrator retrieves only the memory types relevant to
the current agent — never all memory for all agents.
"""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class MemoryRepository:
    """Reads and writes memory_items. Upserts on id so confidence updates
    replace rather than duplicate existing entries."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def upsert(self, memory_id: str, memory_type: str, memory_key: str,
               memory_value: dict, confidence: int,
               source_workflow_run_id: str | None = None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memory_items
                   (id, memory_type, memory_key, memory_value_json,
                    confidence, source_workflow_run_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       memory_value_json = excluded.memory_value_json,
                       confidence = excluded.confidence,
                       updated_at = excluded.updated_at""",
                (memory_id, memory_type, memory_key, json.dumps(memory_value),
                 confidence, source_workflow_run_id, now, now),
            )

    def get_by_type(self, memory_type: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_items WHERE memory_type = ? ORDER BY confidence DESC",
                (memory_type,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_key(self, memory_type: str, memory_key: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM memory_items WHERE memory_type = ? AND memory_key = ?",
                (memory_type, memory_key),
            ).fetchone()
        return dict(row) if row else None
