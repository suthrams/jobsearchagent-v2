"""InterviewCoach — targeted interview preparation for high-value roles.

Pattern: Conditional execution — only runs when triggered.
Trigger conditions (checked by orchestrator, not this agent):
  overall_score >= INTERVIEW_COACH_THRESHOLD  OR  explicit user request.
The agent itself has no knowledge of the trigger — it always produces
a full InterviewPrep when called.

Context keys passed to provider.complete:
  job_id, job_description, resume_profile (dict), job_score (dict),
  research_context (dict), career_advice (dict), final_review (dict)
"""

from app.providers.llm_client import LLMClient
from app.schemas.interview_prep import InterviewPrep
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class InterviewCoach(BaseAgent):
    """Produces a 7-day interview preparation plan grounded in the resume and job."""

    AGENT_NAME = "interview_coach"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> InterviewPrep:
        """Generate interview prep. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, InterviewPrep)
        return InterviewPrep(**result)

    def _output_summary(self, result: dict) -> str:
        topics = len(result.get("likely_interview_topics", []))
        weak = len(result.get("weak_areas_to_defend", []))
        return (
            f"topics={topics} weak_areas={weak} "
            f"confidence={result.get('confidence', '?')}"
        )
