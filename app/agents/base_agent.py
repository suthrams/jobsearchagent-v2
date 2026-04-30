"""BaseAgent — shared timing, observability, and provider dispatch for all agents.

Every concrete agent inherits this class and sets AGENT_NAME, then implements
run() by calling _run() and constructing the appropriate Pydantic schema.

The split between _run() (infrastructure) and run() (schema construction) keeps
observability logic out of concrete agents and makes testing straightforward:
mock the provider, assert on the schema type, verify observability calls.
"""

import time
import logging
from abc import ABC, abstractmethod

from app.providers.llm_client import LLMClient, LLMProviderError
from app.services.observability_service import ObservabilityService

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base for all 8 agents.

    Concrete agents set AGENT_NAME to match the prompt file basename
    (e.g. "scoring_agent" → app/prompts/agents/scoring_agent.txt).
    """

    AGENT_NAME: str = ""

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        self._provider = provider
        self._observability = observability

    # ── Infrastructure layer ──────────────────────────────────────────────────

    def _run(self, workflow_id: str, context: dict, schema: type) -> dict:
        """Dispatch to the provider with timing and observability.

        Emits started → completed on success, started → failed on any exception.
        LLMProviderError is re-raised so the orchestrator can decide whether to
        mark the job as failed or skip it — never swallowed here.
        """
        t0 = time.monotonic()
        event_id = self._observability.log_agent_started(
            workflow_id, self.AGENT_NAME, self._input_summary(context)
        )
        try:
            result = self._provider.complete(agent_name=self.AGENT_NAME, context=context, schema=schema)
            duration_ms = int((time.monotonic() - t0) * 1000)
            self._observability.log_agent_completed(
                workflow_id, self.AGENT_NAME, event_id,
                self._output_summary(result), duration_ms,
            )
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            self._observability.log_agent_failed(
                workflow_id, self.AGENT_NAME, event_id, str(exc), duration_ms,
            )
            raise

    # ── Summary helpers (override for richer log messages) ───────────────────

    def _input_summary(self, context: dict) -> str:
        parts = []
        for key in ("job_id", "resume_id"):
            if key in context:
                parts.append(f"{key}={context[key]}")
        return " ".join(parts) if parts else str(list(context.keys()))

    def _output_summary(self, result: dict) -> str:
        return str(result)[:200]

    # ── Subclass contract ─────────────────────────────────────────────────────

    @abstractmethod
    def run(self, workflow_id: str, context: dict):
        """Execute the agent and return a Pydantic schema instance."""
        ...
