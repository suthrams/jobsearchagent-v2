"""ResearchAgent — gathers company and role signals before scoring.

Pattern: Bounded ReAct (Phase 4 implementation: structured output from job description).
In Phase 4 the agent reasons purely from the provided context. Phase 5 (LangGraph)
will wire in actual tool calls; the output schema is identical either way so no
agent code changes when tools are added.

Context keys passed to provider.complete:
  job_id, job_title, company, source_url, job_description
"""

from app.providers.llm_client import LLMClient
from app.schemas.research_context import ResearchContext
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    """Extracts company, role, and technology signals from the job posting."""

    AGENT_NAME = "research_agent"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> ResearchContext:
        """Research one job. Raises LLMProviderError on unrecoverable failure."""
        result = self._run(workflow_id, context, ResearchContext)
        return ResearchContext(**result)

    def _output_summary(self, result: dict) -> str:
        signals = len(result.get("technology_signals", []))
        flags = len(result.get("risk_flags", []))
        return (
            f"tech_signals={signals} risk_flags={flags} "
            f"confidence={result.get('confidence', '?')}"
        )
