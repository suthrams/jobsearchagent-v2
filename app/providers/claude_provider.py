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

from app.providers.llm_client import LLMClient, LLMProviderError, LLMUsage
from app.providers.prompt_loader import PromptLoader
from app.schemas.resume_profile import (
    CertificationEntry,
    EducationEntry,
    ExperienceEntry,
    SkillGroup,
)

logger = logging.getLogger(__name__)


# Errors that warrant a retry — transient network or server issues.
# AuthenticationError and BadRequestError are intentionally excluded:
# retrying those will always fail with the same result.
_RETRYABLE_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

# Per-million-token pricing is INJECTED via the constructor per ADR-058.
# The catalog lives in config/config.yaml under `models.providers.claude`,
# parsed by `model_registry.catalog_from_config` and threaded through
# `ModelRegistry._build_provider`. This module no longer hardcodes prices.
#
# `_FALLBACK_PRICING` remains as a safety net for tests or code paths that
# construct a ClaudeProvider without going through the registry (and thus
# without injecting pricing). The fallback approximates Sonnet rates.
_FALLBACK_PRICING: dict[str, float] = {"input": 3.00, "output": 15.00}

# Anthropic ephemeral (5-minute) prompt-cache pricing modifiers, applied to the
# base input rate. Without these, every cached call undercounts: cache writes
# bill at 1.25x and cache reads at 0.10x, but neither shows up in input_tokens.
_CACHE_WRITE_MULTIPLIER: float = 1.25
_CACHE_READ_MULTIPLIER: float = 0.10


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
        pricing: dict[str, dict[str, float]] | None = None,
        _model: Any = None,  # inject a mock in tests; production builds ChatAnthropic
    ) -> None:
        self._prompt_loader = prompt_loader
        self._model_name = model_name
        # pricing is keyed by model_name -> {"input": $/M, "output": $/M}, injected
        # by ModelRegistry from the config catalog. When not provided (legacy or
        # test path), _FALLBACK_PRICING is used and a warning is logged on first
        # cost computation.
        self._pricing = pricing or {}
        # _build_chat_model is only imported when _model is not injected,
        # so tests never need langchain_anthropic installed if they inject a mock.
        self._model = _model if _model is not None else self._build_chat_model(model_name)
        # Thread-local storage for last-call token usage — safe for concurrent callers.
        self._tlocal = threading.local()

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def model_name(self) -> str:
        return self._model_name

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
        # ADR-077: the first (parse-failed) attempt was ALSO billed, so carry its
        # usage forward and sum it into the logged total rather than dropping it.
        prior_usage = (0, 0, 0, 0)
        if raw_result.get("parsing_error"):
            prior_usage = self._extract_usage(raw_result)
            raw_result = self._attempt_schema_repair(
                chain, messages, raw_result["parsing_error"]
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._log_call(agent_name, raw_result, elapsed_ms, prior_usage=prior_usage)
        try:
            return self._extract_dict(raw_result)
        except LLMProviderError as exc:
            # ADR-077: schema repair exhausted, but this response WAS billed
            # (usage_metadata is present even on parse failure). Attach the usage
            # (final attempt + the first billed attempt) so BaseAgent can log the
            # otherwise-lost spend on its failure path.
            ti, cc, cr, to = self._extract_usage(raw_result)
            ti += prior_usage[0]
            cc += prior_usage[1]
            cr += prior_usage[2]
            to += prior_usage[3]
            if ti or cc or cr or to:
                exc.usage = LLMUsage(
                    tokens_input=ti + cc + cr,
                    tokens_output=to,
                    cost_usd=self._estimate_cost_with_cache(ti, cc, cr, to),
                    cache_creation_tokens=cc,
                    cache_read_tokens=cr,
                )
            raise

    def count_tokens(self, text: str) -> int:
        """Approximate token count: ~4 characters per token for Claude."""
        return max(1, len(text) // 4)

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Return estimated USD cost using injected per-model pricing."""
        pricing = self._pricing.get(self._model_name, _FALLBACK_PRICING)
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

    def _extract_usage(self, raw_result: dict) -> tuple[int, int, int, int]:
        """Pull token counts from the raw AIMessage.

        Returns (input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens).
        Reads usage_metadata.input_token_details first (LangChain v0.3+ standard),
        then falls back to response_metadata.usage with Anthropic's raw key names
        for older payload shapes. The cache split is required to bill correctly:
        cache writes cost 1.25x input, cache reads cost 0.10x input.
        """
        ai_message = raw_result.get("raw")
        if not ai_message or not hasattr(ai_message, "usage_metadata"):
            return 0, 0, 0, 0
        usage = ai_message.usage_metadata or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)

        details = usage.get("input_token_details") or {}
        cache_creation = int(details.get("cache_creation", 0) or 0)
        cache_read = int(details.get("cache_read", 0) or 0)

        if cache_creation == 0 and cache_read == 0:
            meta = getattr(ai_message, "response_metadata", None) or {}
            raw_usage = meta.get("usage") or {}
            cache_creation = int(raw_usage.get("cache_creation_input_tokens", 0) or 0)
            cache_read = int(raw_usage.get("cache_read_input_tokens", 0) or 0)

        return input_tokens, cache_creation, cache_read, output_tokens

    def _estimate_cost_with_cache(
        self,
        tokens_in: int,
        cache_creation: int,
        cache_read: int,
        tokens_out: int,
    ) -> float:
        """Cost in USD, including Anthropic prompt-cache write/read modifiers."""
        pricing = self._pricing.get(self._model_name, _FALLBACK_PRICING)
        in_rate = pricing["input"]
        out_rate = pricing["output"]
        return (
            tokens_in * in_rate
            + cache_creation * in_rate * _CACHE_WRITE_MULTIPLIER
            + cache_read * in_rate * _CACHE_READ_MULTIPLIER
            + tokens_out * out_rate
        ) / 1_000_000

    # ── Logging ───────────────────────────────────────────────────────────────

    def last_call_usage(self) -> tuple[int, int, float]:
        """Return (tokens_in, tokens_out, cost_usd) for the most recent call in this thread."""
        return getattr(self._tlocal, "last_usage", (0, 0, 0.0))

    def last_call_cache_split(self) -> tuple[int, int]:
        """Return (cache_creation_tokens, cache_read_tokens) for the most recent
        call in this thread. (0, 0) if no call yet or the call had no cache activity.
        """
        return getattr(self._tlocal, "last_cache_split", (0, 0))

    def _log_call(
        self, agent_name: str, raw_result: dict, elapsed_ms: int,
        prior_usage: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        """Log token usage and prompt version to the Python logger, and save to thread-local.

        last_usage stores total_input = regular + cache_creation + cache_read so the
        llm_calls audit row reflects what Anthropic actually counted, not just the
        non-cached slice. Cost is computed against each input category at its own rate.
        last_cache_split preserves the breakdown so callers can compute the cache-hit
        ratio without re-deriving it.

        ``prior_usage`` (ADR-077) is the (input, cache_creation, cache_read, output)
        of an earlier billed attempt on the same logical call (the parse-failed
        attempt before a schema repair). It is added to the final attempt's usage so
        the row reflects what the whole call actually cost, not just the last leg.
        """
        tokens_in, cache_creation, cache_read, tokens_out = self._extract_usage(raw_result)
        tokens_in += prior_usage[0]
        cache_creation += prior_usage[1]
        cache_read += prior_usage[2]
        tokens_out += prior_usage[3]
        cost = self._estimate_cost_with_cache(tokens_in, cache_creation, cache_read, tokens_out)
        total_input = tokens_in + cache_creation + cache_read
        self._tlocal.last_usage = (total_input, tokens_out, cost)
        self._tlocal.last_cache_split = (cache_creation, cache_read)
        version = self._prompt_loader.get_version(agent_name)
        logger.info(
            "llm_call agent=%s model=%s prompt_version=%s "
            "tokens_in=%d (regular=%d cache_w=%d cache_r=%d) tokens_out=%d "
            "cost_usd=%.6f latency_ms=%d",
            agent_name, self._model_name, version,
            total_input, tokens_in, cache_creation, cache_read,
            tokens_out, cost, elapsed_ms,
        )


# ── enhance_fn factory ────────────────────────────────────────────────────────

class _ResumeEnhancement(BaseModel):
    """Structured output schema for the resume_parser LLM enhancement pass.

    Mirrors the public ResumeProfile schema's optional fields. ADR-067 added
    `skill_groups`, `EducationEntry.gpa`, and `EducationEntry.honors` so the
    parser preserves resume content the v1 schema dropped.
    """
    name: str | None = None
    headline: str | None = None
    email: str | None = None
    location: str | None = None
    summary: str | None = None
    experience: list[ExperienceEntry] = []
    skills: list[str] = []
    skill_groups: list[SkillGroup] = []
    education: list[EducationEntry] = []
    certifications: list[CertificationEntry] = []


def make_resume_enhance_fn(
    provider: LLMClient,
    observability: "ObservabilityService | None" = None,
) -> Callable[..., dict]:
    """Return a bound callable compatible with ResumeParser.enhance_fn.

    The orchestrator calls this once at startup and passes the result to
    ResumeParser. ResumeParser itself has no direct dependency on any provider.

    When ``observability`` is provided AND the parser forwards a non-empty
    ``workflow_id`` to the closure, this writes an llm_calls audit row so the
    resume_parser call shows up in the cost rollup. Without that wiring the
    Sonnet enhancement pass billed by Anthropic was previously invisible to
    ``compute_run_totals_from_llm_calls``.

    The closure accepts an optional third arg (``workflow_id``) so legacy
    2-arg invocations from tests / non-workflow callers keep working.
    """
    agent = "resume_parser"

    def enhance(raw_text: str, heuristic_fields: dict, workflow_id: str | None = None) -> dict:
        t0 = time.monotonic()
        result = provider.complete(
            agent_name=agent,
            context={"raw_text": raw_text, "heuristic_fields": heuristic_fields},
            schema=_ResumeEnhancement,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if observability is not None and workflow_id:
            try:
                ti, to, cost = provider.last_call_usage()
            except (AttributeError, TypeError, ValueError):
                ti, to, cost = 0, 0, 0.0
            cc, cr = 0, 0
            try:
                cc, cr = provider.last_call_cache_split()
            except (AttributeError, TypeError, ValueError):
                pass
            try:
                observability.log_llm_call(
                    workflow_id=workflow_id,
                    agent_name=agent,
                    provider=getattr(provider, "provider_name", "unknown"),
                    model=getattr(provider, "model_name", "unknown"),
                    tokens_input=int(ti),
                    tokens_output=int(to),
                    cost_usd=float(cost),
                    latency_ms=latency_ms,
                    cache_creation_tokens=int(cc or 0),
                    cache_read_tokens=int(cr or 0),
                )
            except Exception:
                logger.exception("make_resume_enhance_fn: log_llm_call failed")
        return result

    return enhance
