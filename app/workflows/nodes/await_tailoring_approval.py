"""await_tailoring_approval node — HITL pause #2: user approves/rejects tailored resume."""
from __future__ import annotations

import logging
from typing import Callable

from langgraph.types import interrupt

from app.repositories.database import utcnow_iso

logger = logging.getLogger(__name__)


def make_await_tailoring_approval_node() -> Callable[[dict], dict]:
    def await_tailoring_approval(state: dict) -> dict:
        fidelity: dict | None = state.get("fidelity_review")
        tailored: dict | None = state.get("tailored_resume")

        fidelity_status = (fidelity or {}).get("overall_fidelity_status", "unknown")

        decision = interrupt({
            "decision_type": "approve_tailoring",
            "message": "Review the tailored resume draft and approve or reject.",
            "fidelity_status": fidelity_status,
            "approval_recommendation": (fidelity or {}).get("approval_recommendation", False),
            "tailored_resume": tailored,
        })

        decision_value: str = (decision or {}).get("decision_value", "rejected")

        human_decisions = list(state.get("human_decisions") or [])
        human_decisions.append({
            "decision_type": "approve_tailoring",
            "decision_value": decision_value,
            "payload": {"fidelity_status": fidelity_status},
            "decided_at": utcnow_iso(),
        })

        logger.info("await_tailoring_approval: user decision = %s", decision_value)

        return {
            "pending_decision": None,
            "human_decisions": human_decisions,
            "current_step": "report_generation",
            "updated_at": utcnow_iso(),
        }

    return await_tailoring_approval
