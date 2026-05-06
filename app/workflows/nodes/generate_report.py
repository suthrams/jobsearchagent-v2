"""generate_report node — assembles the final Markdown run report and persists final state."""
from __future__ import annotations

import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.repositories.workflow_repository import WorkflowRepository
from app.services.observability_service import ObservabilityService
from app.services.report_generator import ReportGenerator
from app.workflows.limits import append_error

logger = logging.getLogger(__name__)


def make_generate_report_node(
    report_generator: ReportGenerator,
    observability: ObservabilityService,
    workflow_repo: WorkflowRepository | None = None,
) -> Callable[[dict], dict]:
    def generate_report(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        errors = list(state.get("errors") or [])

        try:
            markdown = report_generator.generate_run_summary(workflow_id)
            update = {
                "report": {"markdown": markdown, "generated_at": utcnow_iso()},
                "status": "completed",
                "errors": errors,
                "current_step": "completed",
                "updated_at": utcnow_iso(),
            }
        except Exception as exc:
            logger.error("generate_report: failed for %s: %s", workflow_id, exc)
            errors = append_error({"errors": errors}, "report_generation", "report_failed",
                                  str(exc), recoverable=False)
            update = {
                "report": None,
                "status": "completed_with_errors",
                "errors": errors,
                "current_step": "completed",
                "updated_at": utcnow_iso(),
            }

        # Persist terminal state to workflow_runs so history shows final status,
        # final metrics, and any errors collected along the way.
        if workflow_repo is not None and workflow_id:
            try:
                final_state = {**state, **update}
                workflow_repo.update_state(workflow_id, final_state)
            except Exception as exc:
                logger.warning("generate_report: workflow_runs update failed for %s: %s",
                               workflow_id, exc)

        # ADR-pending observability fix: roll up the canonical totals from
        # llm_calls (the audit-truth source) and persist them to run_metrics.
        # The in-memory state["run_metrics"] aggregator is lossy — schema-repair
        # retries and interpreter-shutdown failures get billed by the provider
        # but don't always land in state. Reading from llm_calls reconciles
        # closer to the provider's billing console.
        if workflow_id:
            try:
                totals = observability.compute_run_totals_from_llm_calls(workflow_id)
                started_at = state.get("started_at") or utcnow_iso()
                # Best-effort wall-clock; full duration tracking lives in step_executions.
                state_metrics = state.get("run_metrics") or {}
                duration_ms = int(state_metrics.get("total_duration_ms") or 0)
                observability.finalize_run_metrics(
                    workflow_id=workflow_id,
                    total_llm_calls=totals["calls"],
                    total_tokens_input=totals["tokens_input"],
                    total_tokens_output=totals["tokens_output"],
                    total_cost_usd=totals["cost_usd"],
                    total_duration_ms=duration_ms,
                    completed_at=utcnow_iso(),
                )
            except Exception as exc:
                logger.warning("generate_report: finalize_run_metrics failed for %s: %s",
                               workflow_id, exc)

        return update

    return generate_report
