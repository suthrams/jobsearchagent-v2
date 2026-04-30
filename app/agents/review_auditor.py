"""ReviewAuditor — evaluates critique quality and controls the reflection loop.

Pattern: Evaluator / Reflection.
stop_recommendation in the output is the auditor's signal to the orchestrator.
The orchestrator also stops the loop on stagnation (audit_score improvement
< 5 points between rounds) regardless of stop_recommendation.

Context keys passed to provider.complete:
  job_id, resume_review (dict), resume_profile (dict), job_description,
  job_score (dict), review_round (int), max_rounds (int)
"""

from app.providers.llm_client import LLMClient
from app.schemas.review_audit import ReviewAudit
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class ReviewAuditor(BaseAgent):
    """Assesses whether the critique is specific, evidence-based, and actionable."""

    AGENT_NAME = "review_auditor"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> ReviewAudit:
        """Audit the latest critique. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, ReviewAudit)
        return ReviewAudit(**result)

    def _input_summary(self, context: dict) -> str:
        return (
            f"job_id={context.get('job_id', '?')} "
            f"round={context.get('review_round', 1)}"
        )

    def _output_summary(self, result: dict) -> str:
        return (
            f"audit_score={result.get('audit_score', '?')} "
            f"stop={result.get('stop_recommendation', '?')} "
            f"stop_reason={result.get('stop_reason', '')}"
        )
