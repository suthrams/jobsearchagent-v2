"""BaseAgent — shared timing, observability, and provider dispatch for all agents.

Every concrete agent inherits this class and sets AGENT_NAME, then implements
run() by calling _run() and constructing the appropriate Pydantic schema.

The split between _run() (infrastructure) and run() (schema construction) keeps
observability logic out of concrete agents and makes testing straightforward:
mock the provider, assert on the schema type, verify observability calls.
"""

import logging
import threading
import time
from abc import ABC, abstractmethod

from app.providers.llm_client import LLMClient, LLMProviderError, LLMUsage
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
        # Thread-local storage so concurrent callers (e.g. score_jobs workers) each
        # get their own last-call usage without racing on a shared instance variable.
        self._tlocal = threading.local()

    # ── Infrastructure layer ──────────────────────────────────────────────────

    def _run(self, workflow_id: str, context: dict, schema: type) -> dict:
        """Dispatch to the provider with timing and observability.

        Emits started → completed on success, started → failed on any exception.
        LLMProviderError is re-raised so the orchestrator can decide whether to
        mark the job as failed or skip it — never swallowed here.

        Internally uses provider.complete_with_usage() so result + usage arrive
        together (no thread-local race between the two calls). Falls back to the
        legacy two-step path if the provider returns something unexpected (e.g.
        a test double that overrides only complete()).
        """
        t0 = time.monotonic()
        event_id = self._observability.log_agent_started(
            workflow_id, self.AGENT_NAME, self._input_summary(context)
        )
        try:
            llm_t0 = time.monotonic()
            try:
                result, usage = self._provider.complete_with_usage(
                    agent_name=self.AGENT_NAME, context=context, schema=schema,
                )
            except (AttributeError, TypeError, ValueError):
                # Legacy path: provider returns dict, usage fetched separately.
                # Test doubles that only implement complete() land here.
                result = self._provider.complete(
                    agent_name=self.AGENT_NAME, context=context, schema=schema,
                )
                try:
                    ti, to, cost = self._provider.last_call_usage()
                except (AttributeError, TypeError, ValueError):
                    ti, to, cost = 0, 0, 0.0
                usage = LLMUsage(tokens_input=int(ti), tokens_output=int(to), cost_usd=float(cost))
            llm_latency_ms = int((time.monotonic() - llm_t0) * 1000)

            self._tlocal.last_usage = usage
            # Per-call audit row (ADR-pending observability fix). Without this the
            # llm_calls table stays empty and cost attribution is impossible to
            # reconcile against the provider's billing console. Best-effort: failures
            # are swallowed by ObservabilityService so a broken audit trail never
            # crashes a run.
            try:
                self._observability.log_llm_call(
                    workflow_id=workflow_id,
                    agent_name=self.AGENT_NAME,
                    provider=self._provider.provider_name,
                    model=self._provider.model_name,
                    tokens_input=usage.tokens_input,
                    tokens_output=usage.tokens_output,
                    cost_usd=usage.cost_usd,
                    latency_ms=llm_latency_ms,
                )
            except Exception:
                # Already-defensive ObservabilityService logs and swallows; this
                # outer guard catches the unlikely case of attribute lookup failing
                # on a bare-bones test double provider.
                pass

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
            # Convert interpreter-shutdown RuntimeErrors to LLMProviderError so
            # callers that catch LLMProviderError handle this gracefully instead of
            # crashing the whole workflow. This happens when the server is killed
            # while a LangChain/httpx call is in-flight in a background thread.
            if isinstance(exc, RuntimeError) and "interpreter shutdown" in str(exc):
                raise LLMProviderError(f"Provider call interrupted by process shutdown: {exc}") from exc
            raise

    def last_call_usage(self) -> tuple[int, int, float]:
        """Return (tokens_in, tokens_out, cost_usd) for the most recent call in this thread.

        Safe to call from concurrent threads — each thread has its own value.
        Returns (0, 0, 0.0) if no call has been made yet in the current thread.

        DEPRECATED in favor of last_call_usage_typed(). Kept until all callers migrate.
        """
        usage = getattr(self._tlocal, "last_usage", None)
        if usage is None:
            return (0, 0, 0.0)
        return usage.as_tuple()

    def last_call_usage_typed(self) -> LLMUsage:
        """Return the typed LLMUsage for the most recent call in this thread.

        Same thread-safety guarantees as last_call_usage(). Prefer this — the
        positional tuple shape is being phased out (see ADR-055-era migration).
        """
        return getattr(self._tlocal, "last_usage", LLMUsage())

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
