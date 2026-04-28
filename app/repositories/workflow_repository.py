import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class WorkflowRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, workflow_id: str, workflow_type: str, state: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO workflow_runs
                   (id, workflow_type, status, current_step, state_json,
                    user_id, resume_id, started_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    workflow_id,
                    workflow_type,
                    state.get("status", "initialized"),
                    state.get("current_step", "initialized"),
                    json.dumps(state),
                    state.get("user_id"),
                    state.get("resume_id"),
                    now,
                    now,
                ),
            )

    def get_by_id(self, workflow_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE id = ?", (workflow_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def update_state(self, workflow_id: str, state: dict) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE workflow_runs
                   SET status = ?, current_step = ?, state_json = ?,
                       resume_id = ?, selected_job_id = ?, updated_at = ?,
                       completed_at = CASE WHEN ? IN ('completed','failed','cancelled')
                                     THEN ? ELSE completed_at END,
                       error_message = ?
                   WHERE id = ?""",
                (
                    state.get("status"),
                    state.get("current_step"),
                    json.dumps(state),
                    state.get("resume_id"),
                    state.get("selected_job_id"),
                    now,
                    state.get("status"),
                    now,
                    state.get("errors", [{}])[-1].get("message") if state.get("errors") else None,
                    workflow_id,
                ),
            )

    def get_by_status(self, status: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE status = ? ORDER BY started_at DESC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 20) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
