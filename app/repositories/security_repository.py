"""Repository for the security_events table — append-only audit log.

Records prompt injection detections, PII redactions, tool access blocks,
and unsupported claim detections. Retention window is longer than observability
data (180 days vs 30) because security events may be needed for audit review.
"""
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class SecurityRepository:
    """Reads and writes security_events. Append-only — no update or delete methods."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, event_id: str, workflow_run_id: str, event_type: str,
               severity: str, description: str) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO security_events
                   (id, workflow_run_id, event_type, severity, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, workflow_run_id, event_type, severity, description, now),
            )

    def get_by_run(self, workflow_run_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM security_events WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [dict(r) for r in rows]
