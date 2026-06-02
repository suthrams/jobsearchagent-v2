"""Repository for the human_decisions table — every user decision at a HITL checkpoint."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection


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

    def list_for_user(
        self, user_id: str | None = None, days: int | None = None
    ) -> list[dict]:
        """System-level read for the System Dashboard Decisions section (ADR-074).

        Resolves each decision's owning profile via a LEFT JOIN to workflow_runs
        and COALESCEs a missing user_id (orphan rows) to '0' (ADR-062); each row
        carries an ``owner_user_id`` column with that value. ``user_id=None`` =>
        all profiles; ``days=None`` => all-time. Newest first. Returns [] if the
        table is absent.
        """
        clauses: list[str] = []
        params: list = []
        if user_id is not None:
            clauses.append("COALESCE(wr.user_id, '0') = ?")
            params.append(str(user_id))
        if days is not None and days > 0:
            clauses.append(f"hd.decided_at >= datetime('now', '-{int(days)} days')")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            with get_connection(self.db_path) as conn:
                rows = conn.execute(
                    f"""SELECT hd.*, COALESCE(wr.user_id, '0') AS owner_user_id
                        FROM human_decisions hd
                        LEFT JOIN workflow_runs wr ON wr.id = hd.workflow_run_id
                        {where}
                        ORDER BY hd.decided_at DESC""",
                    tuple(params),
                ).fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]
