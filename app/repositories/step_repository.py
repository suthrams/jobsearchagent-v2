"""Repository for the step_executions table — timestamped record of every workflow step.

Distinct from agent_events: step_executions tracks workflow-level step transitions;
agent_events tracks individual LLM agent calls within a step. A single step may
trigger multiple agent calls. Both levels are needed for the observability timeline.
"""
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class StepRepository:
    """Reads and writes step_executions. duration_ms is computed via SQLite julianday()
    arithmetic at complete/fail time so it is always consistent with started_at."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, step_id: str, workflow_run_id: str, step: str) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO step_executions
                   (id, workflow_run_id, step, status, started_at)
                   VALUES (?, ?, ?, 'started', ?)""",
                (step_id, workflow_run_id, step, now),
            )

    def complete(self, step_id: str, notes: str | None = None) -> None:
        completed_at = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE step_executions
                   SET status = 'completed', completed_at = ?,
                       duration_ms = CAST(
                           (julianday(?) - julianday(started_at)) * 86400000 AS INTEGER
                       ),
                       notes = ?
                   WHERE id = ?""",
                (completed_at, completed_at, notes, step_id),
            )

    def fail(self, step_id: str, notes: str | None = None) -> None:
        failed_at = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE step_executions
                   SET status = 'failed', completed_at = ?,
                       duration_ms = CAST(
                           (julianday(?) - julianday(started_at)) * 86400000 AS INTEGER
                       ),
                       notes = ?
                   WHERE id = ?""",
                (failed_at, failed_at, notes, step_id),
            )

    def get_by_run(self, workflow_run_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM step_executions WHERE workflow_run_id = ? ORDER BY started_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [dict(r) for r in rows]
