"""Abstract LLM provider interface.

All agents depend on this interface — never on a concrete provider class.
This makes agents testable without an API key and provider-agnostic so
swapping Claude for another model requires no changes to agent code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMProviderError(Exception):
    """Raised when the provider cannot return a valid response after all retries."""


@dataclass(frozen=True)
class LLMUsage:
    """Token + cost accounting for a single LLM call.

    Frozen so it can be cached / passed across thread boundaries safely.
    Fields are all required; defaults exist only for the zero-usage case
    (no call yet, or a failure path that didn't bill).

    ``tokens_input`` is the union of regular + cache_creation + cache_read so
    the audit row reflects what the provider counted. The cache breakdown is
    kept separately so the Cost Dashboard can compute hit ratios without
    re-deriving them.
    """
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    def as_tuple(self) -> tuple[int, int, float]:
        """Backward-compat shim for callers still using the legacy tuple shape."""
        return (self.tokens_input, self.tokens_output, self.cost_usd)


class LLMClient(ABC):
    """Abstract base class for all LLM provider implementations.

    Concrete implementations: ClaudeProvider (production), OpenAIProvider (real, ADR-053),
    and test doubles injected via the _model / _client constructor parameter.
    """

    # ── Provider identity (ADR pending — observability wiring) ───────────────
    # Default-implemented so legacy test doubles that subclass LLMClient without
    # setting these don't break. Real providers override.
    @property
    def provider_name(self) -> str:
        """Lowercase provider tag, e.g. "claude", "openai". Used for llm_calls.provider."""
        return "unknown"

    @property
    def model_name(self) -> str:
        """Provider-native model id, e.g. "claude-sonnet-4-6". Used for llm_calls.model."""
        return "unknown"

    @abstractmethod
    def complete(self, agent_name: str, context: dict, schema: type) -> dict:
        """Call the LLM and return a validated dict matching the given schema.

        Args:
            agent_name: Identifies the prompt file to load, e.g. "scoring_agent".
                        Must have a matching file in app/prompts/agents/.
            context:    Variables injected into the human message as JSON.
                        Keys and values are agent-specific.
            schema:     Pydantic class defining the expected output shape.
                        The provider validates the model's output against this.

        Returns:
            Validated dict with keys matching the Pydantic schema fields.

        Raises:
            LLMProviderError: If all retries are exhausted or schema repair fails.
        """
        ...

    def complete_with_usage(self, agent_name: str, context: dict, schema: type) -> tuple[dict, LLMUsage]:
        """Call the LLM and return (validated_dict, usage) together — no side-channel race.

        Default implementation calls complete() then last_call_usage() for backward
        compat, then enriches with the cache breakdown from last_call_cache_split()
        when the provider exposes it. Concrete providers MAY override for efficiency.
        """
        data = self.complete(agent_name, context, schema)
        ti, to, cost = self.last_call_usage()
        cc, cr = 0, 0
        try:
            cc, cr = self.last_call_cache_split()
        except (AttributeError, TypeError, ValueError):
            pass
        return data, LLMUsage(
            tokens_input=int(ti),
            tokens_output=int(to),
            cost_usd=float(cost),
            cache_creation_tokens=int(cc or 0),
            cache_read_tokens=int(cr or 0),
        )

    def last_call_cache_split(self) -> tuple[int, int]:
        """Return (cache_creation_tokens, cache_read_tokens) for the most recent
        call in this thread. Defaults to (0, 0) — providers without prompt caching
        (OpenAI today) keep the default; ClaudeProvider overrides.
        """
        return (0, 0)

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Estimate token count for a text string.

        Used for pre-call budget checks. Exact counts come from
        usage_metadata after the call.
        """
        ...

    @abstractmethod
    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Return estimated cost in USD for a call with the given token counts."""
        ...

    @abstractmethod
    def last_call_usage(self) -> tuple[int, int, float]:
        """Return (tokens_in, tokens_out, cost_usd) for the most recent call in this thread.

        Thread-safe: each thread gets its own value. Returns (0, 0, 0.0) if no call
        has been made yet in the current thread.

        DEPRECATED in favor of complete_with_usage(). Kept until all callers migrate.
        """
        ...
