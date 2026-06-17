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

    def list_orphaned_runs(self) -> list[dict]:
        """Runs left non-terminal by a dead process (status running/cancelling), with
        state_json parsed into ``state`` (ADR-096).

        Only register_run and generate_report write workflow_runs, so a run whose
        process died mid-flight is frozen at running/cancelling. Called at startup,
        when the executor + run_control registry are freshly empty, every such row is
        definitively orphaned (its owning process is gone). Oldest first.
        """
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE status IN ('running', 'cancelling') "
                "ORDER BY started_at"
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["state"] = json.loads(d.pop("state_json") or "{}")
            except Exception:
                d["state"] = {}
            out.append(d)
        return out

    def bump_resume_attempt(self, workflow_id: str) -> int:
        """Increment the run's ``state.resume_attempts`` counter and return the new
        value (ADR-096). The counter rides state_json so no schema change is needed;
        it caps how many times startup recovery will resume a run before giving up."""
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM workflow_runs WHERE id = ?", (workflow_id,)
            ).fetchone()
            if row is None:
                return 0
            try:
                state = json.loads(row["state_json"] or "{}")
            except Exception:
                state = {}
            attempts = int(state.get("resume_attempts") or 0) + 1
            state["resume_attempts"] = attempts
            state["updated_at"] = now
            conn.execute(
                "UPDATE workflow_runs SET state_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(state), now, workflow_id),
            )
        return attempts

    def mark_failed(self, workflow_id: str, *, message: str,
                    error_type: str = "ProcessInterrupted") -> bool:
        """Flip a single run to failed in both the status column and the embedded
        state_json, stamping completed_at + error_message (ADR-096). Returns False if
        the run does not exist. Used by startup recovery for runs that exhaust their
        resume attempts, and by reconcile_orphaned_runs for the fail-everything path."""
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM workflow_runs WHERE id = ?", (workflow_id,)
            ).fetchone()
            if row is None:
                return False
            try:
                state = json.loads(row["state_json"] or "{}")
            except Exception:
                state = {}
            state["status"] = "failed"
            errs = list(state.get("errors") or [])
            errs.append({
                "stage": "graph", "error_type": error_type,
                "message": message, "recoverable": False,
            })
            state["errors"] = errs
            state["updated_at"] = now
            conn.execute(
                """UPDATE workflow_runs
                   SET status = 'failed', state_json = ?, updated_at = ?,
                       completed_at = COALESCE(completed_at, ?), error_message = ?
                   WHERE id = ?""",
                (json.dumps(state), now, now, message, workflow_id),
            )
        return True

    def reconcile_orphaned_runs(self, *, message: str) -> list[str]:
        """Mark ALL currently-orphaned runs (running/cancelling) failed; return ids.

        The fail-everything backstop (ADR-096 Layer 3). ADR-096 startup recovery
        prefers resuming orphans from their checkpoint; this is the fallback when no
        durable resume is possible. Terminal + parked runs are never matched.
        """
        ids = [r["id"] for r in self.list_orphaned_runs()]
        for wid in ids:
            self.mark_failed(wid, message=message)
        return ids

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

    def count_for_user(self, user_id: str) -> int:
        """Total runs recorded for a profile. Used as a monotonic per-profile seed for
        the Adzuna interleave rotation (ADR-108 addendum); survives process restarts.
        Best-effort: returns 0 on any error so discovery never fails on a count."""
        try:
            with get_connection(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE user_id = ?",
                    (str(user_id),),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception:  # noqa: BLE001 - a count must never break discovery
            return 0
