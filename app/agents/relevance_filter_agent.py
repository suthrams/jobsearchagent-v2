"""RelevanceFilterAgent — reasons over discovered jobs before scoring (ADR-079).

Pattern: Structured output, ONE batched call per run. Opt-in per profile
(search.relevance_filter). Runs in-graph between load_resume and score_jobs on the
auto-scoring branch, judging each discovered posting for fit RELATIVE to the
profile's own target band (seniority, both directions) and target roles (relevance).

Cheapest tier (Haiku) by default via ModelRegistry: the whole point is to drop
mismatches before paying the 2 LLM calls/job that scoring costs, so the filter must
itself be cheap. One batched call replaces many expensive ones.

Context keys passed to provider.complete:
  _cached.resume_profile (REDACTED via trim_resume_profile), target_roles,
  seniority_signals, jobs[] (job_id, title, company, truncated description)
"""

from app.providers.llm_client import LLMClient
from app.schemas.relevance_filter import RelevanceFilterResult
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class RelevanceFilterAgent(BaseAgent):
    """Keep/drop each discovered job by seniority + role relevance to the profile."""

    AGENT_NAME = "relevance_filter"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> RelevanceFilterResult:
        """Triage the discovered jobs. Raises LLMProviderError on unrecoverable failure
        (the node catches it and keeps all jobs — a filter fault never wipes a run)."""
        result = self._run(workflow_id, context, RelevanceFilterResult)
        return RelevanceFilterResult(**result)

    def _input_summary(self, context: dict) -> str:
        jobs = context.get("jobs") or []
        return f"jobs={len(jobs)}"

    def _output_summary(self, result: dict) -> str:
        verdicts = result.get("verdicts") or []
        dropped = sum(1 for v in verdicts if not v.get("keep", True))
        return f"verdicts={len(verdicts)} dropped={dropped}"
