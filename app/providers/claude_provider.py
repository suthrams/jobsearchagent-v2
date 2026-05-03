"""ClaudeProvider — wraps ChatAnthropic with retry, schema repair, and token logging.

Responsibilities (one private method per concern):
  Prompt assembly    → delegated entirely to PromptLoader
  Chain construction → _build_chain  (adds structured-output wrapper)
  API invocation     → _invoke_with_retry  (tenacity, exponential backoff)
  Schema repair      → _attempt_schema_repair  (one pass on validation failure)
  Result extraction  → _extract_dict  (Pydantic → plain dict)
  Token accounting   → _extract_usage, _log_call  (Python logger; DB in Phase 5)

DB-level logging (llm_calls table) is not wired here — it happens in Phase 5
via the orchestrator, which has access to workflow_id and ObservabilityService.
"""

import logging
import random
import threading
import time
from typing import Any, Callable

import anthropic
import tenacity
from pydantic import BaseModel

from app.providers.llm_client import LLMClient, LLMProviderError
from app.providers.prompt_loader import PromptLoader
from app.schemas.resume_profile import CertificationEntry, EducationEntry, ExperienceEntry

logger = logging.getLogger(__name__)


# Errors that warrant a retry — transient network or server issues.
# AuthenticationError and BadRequestError are intentionally excluded:
# retrying those will always fail with the same result.
_RETRYABLE_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

# Per-million-token pricing used by estimate_cost().
# Update these constants when Anthropic changes pricing.
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.25,  "output": 1.25},
}
_FALLBACK_PRICING: dict[str, float] = {"input": 3.00, "output": 15.00}


class ClaudeProvider(LLMClient):
    """LLMClient backed by Anthropic's Claude via langchain-anthropic.

    Usage (production):
        loader = PromptLoader()
        provider = ClaudeProvider(loader, model_name="claude-haiku-4-5-20251001")
        result = provider.complete("scoring_agent", context, JobScore)

    Usage (tests) — inject a mock model to avoid real API calls:
        provider = ClaudeProvider(loader, _model=mock_chat_model)
    """

    _MAX_RETRIES: int = 6
    _RETRY_WAIT_MIN: int = 2   # seconds before first retry
    _RETRY_WAIT_MAX: int = 60  # ceiling for exponential backoff
    _RETRY_AFTER_CAP: int = 90  # never honor retry-after longer than this

    def __init__(
        self,
        prompt_loader: PromptLoader,
        model_name: str = "claude-haiku-4-5-20251001",
        *,
        _model: Any = None,  # inject a mock in tests; production builds ChatAnthropic
    ) -> None:
        self._prompt_loader = prompt_loader
        self._model_name = model_name
        # _build_chat_model is only imported when _model is not injected,
        # so tests never need langchain_anthropic installed if they inject a mock.
        self._model = _model if _model is not None else self._build_chat_model(model_name)
        # Thread-local storage for last-call token usage — safe for concurrent callers.
        self._tlocal = threading.local()

    # ── Public interface (LLMClient contract) ─────────────────────────────────

    def complete(self, agent_name: str, context: dict, schema: type) -> dict:
        """Assemble prompt → call Claude → validate output → return dict.

        Flow:
          1. PromptLoader assembles [SystemMessage, HumanMessage]
          2. Chain = model.with_structured_output(schema, include_raw=True)
          3. Invoke with tenacity retry on transient API errors
          4. If parsing_error, attempt one schema repair pass
          5. Log token usage and prompt version
          6. Return parsed.model_dump()

        Args:
            agent_name: Matches a file in app/prompts/agents/{agent_name}.txt
            context:    Input variables serialized as JSON in the human message
            schema:     Pydantic class; defines and validates the output shape

        Returns:
            Validated dict with keys matching the Pydantic schema.

        Raises:
            LLMProviderError: All retries failed, or schema repair also failed.
        """
        messages = self._prompt_loader.assemble(agent_name, context)
        chain = self._build_chain(schema)
        start = time.monotonic()

        raw_result = self._invoke_with_retry(chain, messages)

        # If the model returned well-formed JSON but wrong structure,
        # try once more with the validation error appended to the conversation.
        if raw_result.get("parsing_error"):
            raw_result = self._attempt_schema_repair(
                chain, messages, raw_result["parsing_error"]
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._log_call(agent_name, raw_result, elapsed_ms)
        return self._extract_dict(raw_result)

    def count_tokens(self, text: str) -> int:
        """Approximate token count: ~4 characters per token for Claude."""
        return max(1, len(text) // 4)

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Return estimated USD cost using per-model pricing table."""
        pricing = _PRICING.get(self._model_name, _FALLBACK_PRICING)
        return (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000

    # ── Chain construction ────────────────────────────────────────────────────

    @staticmethod
    def _build_chat_model(model_name: str):
        """Construct ChatAnthropic. Imported lazily so test-only code never loads it."""
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name, max_tokens=4096)

    def _build_chain(self, schema: type):
        """Wrap the model with structured output and include_raw=True.

        include_raw returns {"raw": AIMessage, "parsed": schema_obj, "parsing_error": ...}
        The raw AIMessage carries usage_metadata (token counts) even on parse failures.
        """
        return self._model.with_structured_output(schema, include_raw=True)

    # ── Invocation ────────────────────────────────────────────────────────────

    def _invoke_with_retry(self, chain, messages: list) -> dict:
        """Invoke the chain with exponential-backoff retry on transient API errors.

        Retries on: APIConnectionError, RateLimitError, InternalServerError.
        Does NOT retry AuthenticationError or BadRequestError — those won't heal.

        On 429s, honors the server's retry-after header (capped at _RETRY_AFTER_CAP)
        when present; otherwise falls back to jittered exponential backoff.
        """
        for attempt in tenacity.Retrying(
            stop=tenacity.stop_after_attempt(self._MAX_RETRIES),
            wait=self._compute_backoff,
            retry=tenacity.retry_if_exception_type(_RETRYABLE_ERRORS),
            reraise=True,
            before_sleep=self._log_retry,
        ):
            with attempt:
                return chain.invoke(messages)

    def _compute_backoff(self, retry_state: "tenacity.RetryCallState") -> float:
        """Pick a wait time for the next attempt.

        Priority:
          1. Anthropic 429 with `retry-after` header → honor it (capped).
          2. Otherwise → jittered exponential, base 2s, cap 60s.
        """
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        retry_after = self._extract_retry_after(exc)
        if retry_after is not None:
            return min(retry_after, self._RETRY_AFTER_CAP)
        # exponential 2, 4, 8, 16, 32, 60 — plus 0–1s jitter
        base = min(self._RETRY_WAIT_MIN * (2 ** (retry_state.attempt_number - 1)),
                   self._RETRY_WAIT_MAX)
        return base + random.random()

    @staticmethod
    def _extract_retry_after(exc: BaseException | None) -> float | None:
        """Read the retry-after header from an Anthropic exception, if present."""
        if not isinstance(exc, anthropic.RateLimitError):
            return None
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        for key in ("retry-after", "Retry-After", "anthropic-ratelimit-tokens-reset"):
            raw = headers.get(key)
            if not raw:
                continue
            try:
                return max(1.0, float(raw))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _log_retry(retry_state: "tenacity.RetryCallState") -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        wait = getattr(retry_state.next_action, "sleep", 0.0)
        logger.warning(
            "claude retry attempt=%d wait=%.1fs error=%s",
            retry_state.attempt_number,
            wait,
            type(exc).__name__ if exc else "?",
        )

    def _attempt_schema_repair(
        self, chain, messages: list, error: object
    ) -> dict:
        """Re-invoke with the validation error appended so Claude can self-correct.

        This fires at most once. If the repair pass also returns a parsing_error,
        _extract_dict will raise LLMProviderError.
        """
        from langchain_core.messages import HumanMessage
        repair_hint = HumanMessage(
            content=(
                f"Your previous response failed schema validation:\n{error}\n\n"
                "Return corrected JSON that exactly matches the required schema. "
                "No extra keys, no missing keys, correct value types."
            )
        )
        return chain.invoke([*messages, repair_hint])

    # ── Result and usage extraction ───────────────────────────────────────────

    def _extract_dict(self, raw_result: dict) -> dict:
        """Convert the validated Pydantic object to a plain dict.

        Raises LLMProviderError if parsing_error is still set after repair.
        """
        if raw_result.get("parsing_error"):
            raise LLMProviderError(
                f"Schema repair failed — model could not produce valid output. "
                f"Error: {raw_result['parsing_error']}"
            )
        return raw_result["parsed"].model_dump()

    def _extract_usage(self, raw_result: dict) -> tuple[int, int]:
        """Pull input/output token counts from the raw AIMessage's usage_metadata."""
        ai_message = raw_result.get("raw")
        if not ai_message or not hasattr(ai_message, "usage_metadata"):
            return 0, 0
        usage = ai_message.usage_metadata or {}
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    # ── Logging ───────────────────────────────────────────────────────────────

    def last_call_usage(self) -> tuple[int, int, float]:
        """Return (tokens_in, tokens_out, cost_usd) for the most recent call in this thread."""
        return getattr(self._tlocal, "last_usage", (0, 0, 0.0))

    def _log_call(self, agent_name: str, raw_result: dict, elapsed_ms: int) -> None:
        """Log token usage and prompt version to the Python logger, and save to thread-local."""
        tokens_in, tokens_out = self._extract_usage(raw_result)
        cost = self.estimate_cost(tokens_in, tokens_out)
        self._tlocal.last_usage = (tokens_in, tokens_out, cost)
        version = self._prompt_loader.get_version(agent_name)
        logger.info(
            "llm_call agent=%s model=%s prompt_version=%s "
            "tokens_in=%d tokens_out=%d cost_usd=%.6f latency_ms=%d",
            agent_name, self._model_name, version,
            tokens_in, tokens_out, cost, elapsed_ms,
        )


# ── enhance_fn factory ────────────────────────────────────────────────────────

class _ResumeEnhancement(BaseModel):
    """Structured output schema for the resume_parser LLM enhancement pass."""
    name: str | None = None
    headline: str | None = None
    email: str | None = None
    location: str | None = None
    summary: str | None = None
    experience: list[ExperienceEntry] = []
    skills: list[str] = []
    education: list[EducationEntry] = []
    certifications: list[CertificationEntry] = []


def make_resume_enhance_fn(provider: LLMClient) -> Callable[[str, dict], dict]:
    """Return a bound callable compatible with ResumeParser.enhance_fn.

    The orchestrator (Phase 5) calls this once at startup and passes the result
    to ResumeParser. ResumeParser itself has no direct dependency on any provider.

    The returned function:
      - Receives raw_text (full resume text) and heuristic_fields (dict from
        the heuristic parser pass)
      - Asks Claude to verify and enrich the heuristic fields, not re-parse
        from scratch — this keeps the output grounded in the actual resume text
      - Returns a plain dict; ResumeParser merges it into the ResumeProfile

    Args:
        provider: Any LLMClient implementation (ClaudeProvider in production,
                  mock in tests).

    Returns:
        Callable[[str, dict], dict] matching the enhance_fn contract.
    """
    def enhance(raw_text: str, heuristic_fields: dict) -> dict:
        return provider.complete(
            agent_name="resume_parser",
            context={"raw_text": raw_text, "heuristic_fields": heuristic_fields},
            schema=_ResumeEnhancement,
        )
    return enhance
