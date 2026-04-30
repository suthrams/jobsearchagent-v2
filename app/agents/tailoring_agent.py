"""TailoringAgent — evidence-bound resume improvement suggestions.

Pattern: Evidence-bound generation — every suggestion must be anchored
to the source resume. claim_type="gap" means the experience does not exist;
those bullets must be labelled as gaps, never rewritten as if present.

FidelityReviewer MUST always run after this agent. The orchestrator enforces
this pairing — it is never optional.

Context keys passed to provider.complete:
  job_id, resume_id, job_description, resume_profile (dict),
  final_review (dict), career_advice (dict)
"""

from app.providers.llm_client import LLMClient
from app.schemas.tailored_resume_draft import TailoredResumeDraft
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class TailoringAgent(BaseAgent):
    """Generates evidence-bound resume improvement suggestions for a specific job."""

    AGENT_NAME = "tailoring_agent"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> TailoredResumeDraft:
        """Generate tailoring suggestions. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, TailoredResumeDraft)
        return TailoredResumeDraft(**result)

    def _output_summary(self, result: dict) -> str:
        bullets = len(result.get("experience_bullet_suggestions", []))
        risk = result.get("fidelity_risk_summary", "")[:80]
        return f"bullets={bullets} risk_summary={risk!r}"
