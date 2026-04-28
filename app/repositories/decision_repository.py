"""Repository for the human_decisions table — every user decision at a HITL checkpoint."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class DecisionRepository:
    """Reads and writes human_decisions. presented_at and decided_at are both required —
    their difference measures user decision latency and detects abandoned workflows."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, decision_id: str, workflow_run_id: str, decision_type: str,
               decision_value: str, payload: dict,
               presented_at: str, decided_at: str) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO human_decisions
                   (id, workflow_run_id, decision_type, decision_value,
                    payload_json, presented_at, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    workflow_run_id,
                    decision_type,
                    decision_value,
                    json.dumps(payload),
                    presented_at,
                    decided_at,
                ),
            )

    def get_by_run(self, workflow_run_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM human_decisions WHERE workflow_run_id = ? ORDER BY decided_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [dict(r) for r in rows]
