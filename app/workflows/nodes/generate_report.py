"""generate_report node — assembles the final Markdown run report."""
from __future__ import annotations

import logging
from typing import Callable

from app.repositories.database import utcnow_iso
from app.services.observability_service import ObservabilityService
from app.services.report_generator import ReportGenerator
from app.workflows.limits import append_error

logger = logging.getLogger(__name__)


def make_generate_report_node(
    report_generator: ReportGenerator,
    observability: ObservabilityService,
) -> Callable[[dict], dict]:
    def generate_report(state: dict) -> dict:
        workflow_id: str = state.get("workflow_id", "")
        errors = list(state.get("errors") or [])

        try:
            markdown = report_generator.generate_run_summary(workflow_id)
        except Exception as exc:
            logger.error("generate_report: failed for %s: %s", workflow_id, exc)
            errors = append_error({"errors": errors}, "report_generation", "report_failed",
                                  str(exc), recoverable=False)
            return {
                "report": None,
                "status": "completed_with_errors",
                "errors": errors,
                "current_step": "completed",
                "updated_at": utcnow_iso(),
            }

        return {
            "report": {"markdown": markdown, "generated_at": utcnow_iso()},
            "status": "completed",
            "errors": errors,
            "current_step": "completed",
            "updated_at": utcnow_iso(),
        }

    return generate_report
