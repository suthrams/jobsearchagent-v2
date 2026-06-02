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

    def list_for_user(
        self, user_id: str | None = None, days: int | None = None
    ) -> list[dict]:
        """System-level read for the unified dashboard (ADR-073).

        Resolves each event's owning profile via a LEFT JOIN to workflow_runs and
        COALESCEs a missing user_id (run-less SYSTEM_RUN_ID sentinel events, and
        pre-ADR-062 orphans) to '0' (ADR-062). Each returned row carries an
        ``owner_user_id`` column with that resolved value.

        - ``user_id=None`` -> all profiles (system-wide view).
        - ``user_id="0"``  -> the default profile plus all sentinel/orphan events.
        - ``days=None``    -> all-time; otherwise the trailing N-day window.

        Newest first. Returns [] if the table does not exist yet.
        """
        clauses: list[str] = []
        params: list = []
        if user_id is not None:
            clauses.append("COALESCE(wr.user_id, '0') = ?")
            params.append(str(user_id))
        if days is not None and days > 0:
            clauses.append(f"se.created_at >= datetime('now', '-{int(days)} days')")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            with get_connection(self.db_path) as conn:
                rows = conn.execute(
                    f"""SELECT se.*, COALESCE(wr.user_id, '0') AS owner_user_id
                        FROM security_events se
                        LEFT JOIN workflow_runs wr ON wr.id = se.workflow_run_id
                        {where}
                        ORDER BY se.created_at DESC""",
                    tuple(params),
                ).fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]
