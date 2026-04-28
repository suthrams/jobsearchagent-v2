"""Repository for agent_events, llm_calls, and run_metrics observability tables."""
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ObservabilityRepository:
    """Reads and writes agent_events, llm_calls, and run_metrics.
    run_metrics is a single rolled-up row per run; create_run_metrics() initialises it
    at run start and update_run_metrics() is called incrementally as agents complete."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create_agent_event(self, event_id: str, workflow_run_id: str, agent_name: str,
                           event_type: str, status: str, duration_ms: int | None = None,
                           input_summary: str | None = None,
                           output_summary: str | None = None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO agent_events
                   (id, workflow_run_id, agent_name, event_type,
                    input_summary, output_summary, status, duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, workflow_run_id, agent_name, event_type,
                 input_summary, output_summary, status, duration_ms, now),
            )

    def create_llm_call(self, call_id: str, workflow_run_id: str, agent_name: str,
                        provider: str, model: str, tokens_input: int, tokens_output: int,
                        estimated_cost: float, latency_ms: int) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO llm_calls
                   (id, workflow_run_id, agent_name, provider, model,
                    tokens_input, tokens_output, estimated_cost, latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (call_id, workflow_run_id, agent_name, provider, model,
                 tokens_input, tokens_output, estimated_cost, latency_ms, now),
            )

    def create_run_metrics(self, metrics_id: str, workflow_run_id: str,
                           started_at: str) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO run_metrics
                   (id, workflow_run_id, total_llm_calls, total_tokens_input,
                    total_tokens_output, total_cost, total_duration_ms,
                    started_at, completed_at, created_at)
                   VALUES (?, ?, 0, 0, 0, 0.0, 0, ?, NULL, ?)""",
                (metrics_id, workflow_run_id, started_at, now),
            )

    def update_run_metrics(self, workflow_run_id: str, total_llm_calls: int,
                           total_tokens_input: int, total_tokens_output: int,
                           total_cost: float, total_duration_ms: int,
                           completed_at: str | None = None) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE run_metrics
                   SET total_llm_calls = ?, total_tokens_input = ?,
                       total_tokens_output = ?, total_cost = ?,
                       total_duration_ms = ?, completed_at = ?
                   WHERE workflow_run_id = ?""",
                (total_llm_calls, total_tokens_input, total_tokens_output,
                 total_cost, total_duration_ms, completed_at, workflow_run_id),
            )

    def get_events_by_run(self, workflow_run_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM agent_events WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [dict(r) for r in rows]
