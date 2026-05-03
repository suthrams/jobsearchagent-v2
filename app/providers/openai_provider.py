"""OpenAIProvider — real implementation of the LLMClient contract on top of the openai SDK.

Per ADR-053. Mirrors ClaudeProvider's responsibilities:
  Chain construction → response_format=json_schema (OpenAI structured output)
  API invocation     → tenacity retry on transient API errors
  Schema repair      → one self-correction pass on validation failure
  Result extraction  → JSON parse → Pydantic validate → dict
  Token accounting   → response.usage; per-thread last-call cache for cost rollup

Pricing tables and registered models are kept here so adding a model is a
two-line edit (registry entry + price row).
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any

import openai
import tenacity
from pydantic import BaseModel, ValidationError

from app.providers.llm_client import LLMClient, LLMProviderError
from app.providers.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

# Errors that warrant a retry — same shape as ClaudeProvider's policy.
_RETRYABLE_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)

# Per-million-token pricing. Indicative values; update when OpenAI changes pricing.
_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15,  "output": 0.60},
    "gpt-4o":      {"input": 2.50,  "output": 10.00},
    "o1":          {"input": 15.00, "output": 60.00},
}
_FALLBACK_PRICING: dict[str, float] = {"input": 2.50, "output": 10.00}


class OpenAIProvider(LLMClient):
    """LLMClient backed by OpenAI's Chat Completions API with json_schema response format.

    Usage (production):
        loader = PromptLoader()
        provider = OpenAIProvider(loader, model_name="gpt-4o-mini")
        result = provider.complete("scoring_agent", context, JobScore)

    Usage (tests) — inject a mock client:
        provider = OpenAIProvider(loader, _client=mock_openai_client)
    """

    _MAX_RETRIES: int = 6
    _RETRY_WAIT_MIN: int = 2
    _RETRY_WAIT_MAX: int = 60
    _RETRY_AFTER_CAP: int = 90

    def __init__(
        self,
        prompt_loader: PromptLoader,
        model_name: str = "gpt-4o-mini",
        *,
        _client: Any = None,
    ) -> None:
        self._prompt_loader = prompt_loader
        self._model_name = model_name
        # _client is the real openai.OpenAI client unless tests inject a mock.
        self._client = _client if _client is not None else openai.OpenAI()
        self._tlocal = threading.local()

    # ── Public interface (LLMClient contract) ─────────────────────────────────

    def complete(self, agent_name: str, context: dict, schema: type) -> dict:
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise LLMProviderError(
                "OpenAIProvider.complete requires a Pydantic BaseModel subclass as schema."
            )

        messages = self._assemble_openai_messages(agent_name, context)
        json_schema = self._build_json_schema(schema)
        start = time.monotonic()

        response, parsed = self._invoke_with_retry(messages, schema, json_schema)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._log_call(agent_name, response, elapsed_ms)
        return parsed.model_dump()

    def count_tokens(self, text: str) -> int:
        # tiktoken is best-effort; on encoding miss we fall back to char/4.
        try:
            import tiktoken
            try:
                encoding = tiktoken.encoding_for_model(self._model_name)
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            return max(1, len(text) // 4)

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        pricing = _PRICING.get(self._model_name, _FALLBACK_PRICING)
        return (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000

    def last_call_usage(self) -> tuple[int, int, float]:
        return getattr(self._tlocal, "last_usage", (0, 0, 0.0))

    # ── Prompt assembly: convert LangChain messages to OpenAI shape ───────────

    def _assemble_openai_messages(self, agent_name: str, context: dict) -> list[dict]:
        """Reuse PromptLoader.assemble() (returns LangChain messages) and convert to OpenAI shape.

        Cache_control hints inside SystemMessage content blocks (used by Claude prompt
        caching) are flattened to plain text — OpenAI ignores them.
        """
        lc_messages = self._prompt_loader.assemble(agent_name, context)
        out: list[dict] = []
        for msg in lc_messages:
            role = "user"
            cls_name = type(msg).__name__
            if cls_name == "SystemMessage":
                role = "system"
            elif cls_name == "AIMessage":
                role = "assistant"
            content = msg.content
            if isinstance(content, list):
                # LangChain may pass [{"type":"text","text":"...","cache_control":...}]
                content = " ".join(
                    blk.get("text", "") if isinstance(blk, dict) else str(blk)
                    for blk in content
                )
            out.append({"role": role, "content": content})
        return out

    # ── JSON-schema construction for OpenAI structured output ────────────────

    @staticmethod
    def _build_json_schema(schema: type[BaseModel]) -> dict:
        """Build a response_format payload from a Pydantic schema.

        OpenAI's structured output requires `additionalProperties: false` on every
        object — we walk the generated schema and force that.
        """
        raw = schema.model_json_schema()
        # Inline $defs / $refs are common in nested schemas; OpenAI accepts them.
        OpenAIProvider._enforce_no_additional_props(raw)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": False,  # strict=true rejects unions / optionals; keep loose for our schemas
                "schema": raw,
            },
        }

    @staticmethod
    def _enforce_no_additional_props(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "additionalProperties" not in node:
                node["additionalProperties"] = False
            for v in node.values():
                OpenAIProvider._enforce_no_additional_props(v)
        elif isinstance(node, list):
            for item in node:
                OpenAIProvider._enforce_no_additional_props(item)

    # ── Invocation with retry + schema repair ─────────────────────────────────

    def _invoke_with_retry(
        self, messages: list[dict], schema: type[BaseModel], response_format: dict,
    ) -> tuple[Any, BaseModel]:
        """Invoke with exponential backoff + retry-after, then schema-validate.

        Returns (raw response object, parsed Pydantic instance).
        Falls back to one schema-repair pass on ValidationError.
        """
        for attempt in tenacity.Retrying(
            stop=tenacity.stop_after_attempt(self._MAX_RETRIES),
            wait=self._compute_backoff,
            retry=tenacity.retry_if_exception_type(_RETRYABLE_ERRORS),
            reraise=True,
            before_sleep=self._log_retry,
        ):
            with attempt:
                response = self._client.chat.completions.create(
                    model=self._model_name,
                    messages=messages,
                    response_format=response_format,
                )
                content = response.choices[0].message.content or ""
                try:
                    parsed = schema.model_validate_json(content)
                    return response, parsed
                except ValidationError as exc:
                    repaired = self._attempt_schema_repair(
                        messages, schema, response_format, content, exc,
                    )
                    if repaired is not None:
                        return response, repaired
                    raise LLMProviderError(
                        f"OpenAI schema repair failed for {schema.__name__}: {exc}"
                    )

    def _attempt_schema_repair(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        response_format: dict,
        bad_content: str,
        error: ValidationError,
    ) -> BaseModel | None:
        """One-shot self-correction pass: hand the model its mistake and ask for a fix."""
        repair_messages = messages + [
            {"role": "assistant", "content": bad_content},
            {"role": "user", "content": (
                f"Your previous response failed schema validation:\n{error}\n\n"
                "Return corrected JSON that exactly matches the required schema. "
                "No extra keys, no missing keys, correct value types."
            )},
        ]
        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=repair_messages,
                response_format=response_format,
            )
            content = response.choices[0].message.content or ""
            return schema.model_validate_json(content)
        except (ValidationError, *_RETRYABLE_ERRORS):
            return None

    def _compute_backoff(self, retry_state: "tenacity.RetryCallState") -> float:
        """Same policy as ClaudeProvider — honor retry-after on 429s, jittered exp otherwise."""
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        retry_after = self._extract_retry_after(exc)
        if retry_after is not None:
            return min(retry_after, self._RETRY_AFTER_CAP)
        base = min(
            self._RETRY_WAIT_MIN * (2 ** (retry_state.attempt_number - 1)),
            self._RETRY_WAIT_MAX,
        )
        return base + random.random()

    @staticmethod
    def _extract_retry_after(exc: BaseException | None) -> float | None:
        if not isinstance(exc, openai.RateLimitError):
            return None
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        for key in ("retry-after", "Retry-After", "x-ratelimit-reset-tokens"):
            raw = headers.get(key) if hasattr(headers, "get") else None
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
            "openai retry attempt=%d wait=%.1fs error=%s",
            retry_state.attempt_number, wait,
            type(exc).__name__ if exc else "?",
        )

    # ── Usage accounting ──────────────────────────────────────────────────────

    def _log_call(self, agent_name: str, response: Any, elapsed_ms: int) -> None:
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0
        cost = self.estimate_cost(tokens_in, tokens_out)
        self._tlocal.last_usage = (tokens_in, tokens_out, cost)
        version = self._prompt_loader.get_version(agent_name)
        logger.info(
            "llm_call provider=openai agent=%s model=%s prompt_version=%s "
            "tokens_in=%d tokens_out=%d cost_usd=%.6f latency_ms=%d",
            agent_name, self._model_name, version,
            tokens_in, tokens_out, cost, elapsed_ms,
        )
