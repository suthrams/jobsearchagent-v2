"""Abstract LLM provider interface.

All agents depend on this interface — never on a concrete provider class.
This makes agents testable without an API key and provider-agnostic so
swapping Claude for another model requires no changes to agent code.
"""

from abc import ABC, abstractmethod


class LLMProviderError(Exception):
    """Raised when the provider cannot return a valid response after all retries."""


class LLMClient(ABC):
    """Abstract base class for all LLM provider implementations.

    Concrete implementations: ClaudeProvider (production), OpenAIProvider (stub),
    and test doubles injected via the _model constructor parameter.
    """

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
        """
        ...
