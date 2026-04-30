"""ScoringAgent — scores a single job against the resume profile.

Pattern: Structured output — no tools, no reflection, no loop.
Runs once per job in the batch. Uses haiku model by default (cheapest call
in the system — up to MAX_JOBS_PER_RUN = 20 calls per run).

Context keys passed to provider.complete:
  job_id, resume_id, job_title, company, job_description,
  resume_profile (dict), career_track, research_context (dict | None)
"""

from app.providers.llm_client import LLMClient
from app.schemas.job_score import JobScore
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class ScoringAgent(BaseAgent):
    """Scores one job/resume pair across five fit dimensions (0–100)."""

    AGENT_NAME = "scoring_agent"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> JobScore:
        """Score one job. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, JobScore)
        return JobScore(**result)

    def _output_summary(self, result: dict) -> str:
        return (
            f"overall={result.get('overall_score', '?')} "
            f"technical={result.get('technical_score', '?')} "
            f"confidence={result.get('confidence', '?')}"
        )
