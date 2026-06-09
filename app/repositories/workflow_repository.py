"""Repository for the workflow_runs table — the central execution record."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class WorkflowRepository:
    """Reads and writes workflow_runs. Stores the full WorkflowState snapshot in state_json."""
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

    def reconcile_orphaned_runs(self, *, message: str) -> list[str]:
        """Mark runs left non-terminal by a dead process as failed. Returns the ids.

        A workflow executes in an in-process thread pool; only the register_run and
        generate_report nodes write workflow_runs. So if the API process dies mid-run
        (restart, crash, or interpreter shutdown -> 'cannot schedule new futures after
        interpreter shutdown'), the row is frozen at running/cancelling and the UI
        shows it as perpetually running. Called once at startup, when the executor and
        run_control registry are freshly empty: any running/cancelling row is then
        definitively orphaned (its owning process is gone), so flip it to failed with a
        note and stamp completed_at + error_message. The embedded state_json status is
        updated too, with an appended error, so state readers agree with the column.

        Single-process assumption: correct for the standard one-worker uvicorn and for
        --reload (only the killed worker's runs are orphaned). A true multi-worker
        deploy would need a shared run registry before enabling this.
        """
        now = utcnow_iso()
        reconciled: list[str] = []
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, state_json FROM workflow_runs "
                "WHERE status IN ('running', 'cancelling')"
            ).fetchall()
            for r in rows:
                wid = r["id"]
                try:
                    state = json.loads(r["state_json"] or "{}")
                except Exception:
                    state = {}
                state["status"] = "failed"
                errs = list(state.get("errors") or [])
                errs.append({
                    "stage": "graph", "error_type": "ProcessInterrupted",
                    "message": message, "recoverable": False,
                })
                state["errors"] = errs
                state["updated_at"] = now
                conn.execute(
                    """UPDATE workflow_runs
                       SET status = 'failed', state_json = ?, updated_at = ?,
                           completed_at = COALESCE(completed_at, ?), error_message = ?
                       WHERE id = ?""",
                    (json.dumps(state), now, now, message, wid),
                )
                reconciled.append(wid)
        return reconciled

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
