"""CareerAdvisor — strategic career guidance after the reflection loop completes.

Pattern: Advisory reasoning — one-shot structured output.
The critical invariant: resume_gaps and career_gaps must never be conflated.
  resume_gaps   = experience exists but is poorly expressed (tailoring can help)
  career_gaps   = capability is genuinely missing (tailoring cannot help)

Context keys passed to provider.complete:
  job_id, resume_id, job_description, resume_profile (dict),
  final_review (dict), job_score (dict), career_track (str)
"""

from app.providers.llm_client import LLMClient
from app.schemas.career_advice import CareerAdvice
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class CareerAdvisor(BaseAgent):
    """Produces actionable positioning advice with resume/career gap separation."""

    AGENT_NAME = "career_advisor"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> CareerAdvice:
        """Generate career advice. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, CareerAdvice)
        return CareerAdvice(**result)

    def _output_summary(self, result: dict) -> str:
        resume_gaps = len(result.get("resume_gaps", []))
        career_gaps = len(result.get("career_gaps", []))
        return (
            f"resume_gaps={resume_gaps} career_gaps={career_gaps} "
            f"confidence={result.get('confidence', '?')}"
        )
