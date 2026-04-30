"""FidelityReviewer — final safety gate before tailored content reaches the user.

Pattern: Validation / Guardrail.
Always runs after TailoringAgent. approval_recommendation drives the HITL
decision: "reject" means the draft must not be shown regardless of what the
user requests. The orchestrator enforces this — it never bypasses the reviewer.

Context keys passed to provider.complete:
  job_id, resume_id, job_description, resume_profile (dict), tailored_draft (dict)
"""

from app.providers.llm_client import LLMClient
from app.schemas.fidelity_review import FidelityReview
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class FidelityReviewer(BaseAgent):
    """Validates every tailoring suggestion against the source resume."""

    AGENT_NAME = "fidelity_reviewer"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> FidelityReview:
        """Validate tailored draft. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, FidelityReview)
        return FidelityReview(**result)

    def _output_summary(self, result: dict) -> str:
        status = result.get("overall_fidelity_status", "?")
        recommendation = result.get("approval_recommendation", "?")
        unsupported = len(result.get("unsupported_claims", []))
        return (
            f"status={status} recommendation={recommendation} "
            f"unsupported_claims={unsupported}"
        )
