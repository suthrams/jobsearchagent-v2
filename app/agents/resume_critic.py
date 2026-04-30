"""ResumeCritic — section-level critique of the resume against a selected job.

Pattern: Critique — one-shot structured output that feeds the reflection loop.
Runs only on high-match jobs. Receives prior_audit_feedback on rounds 2 and 3
so each round builds on the auditor's previous instructions.

Context keys passed to provider.complete:
  job_id, resume_id, job_description, resume_profile (dict), job_score (dict),
  research_context (dict), prior_audit_feedback (str | None), review_round (int)
"""

from app.providers.llm_client import LLMClient
from app.schemas.resume_review import ResumeReview
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class ResumeCritic(BaseAgent):
    """Identifies resume gaps, weak positioning, and improvement opportunities."""

    AGENT_NAME = "resume_critic"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> ResumeReview:
        """Critique the resume. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, ResumeReview)
        return ResumeReview(**result)

    def _input_summary(self, context: dict) -> str:
        return (
            f"job_id={context.get('job_id', '?')} "
            f"round={context.get('review_round', 1)}"
        )

    def _output_summary(self, result: dict) -> str:
        sections = len(result.get("section_reviews", []))
        gaps = len(result.get("critical_gaps", []))
        return (
            f"sections={sections} critical_gaps={gaps} "
            f"confidence={result.get('confidence', '?')}"
        )
