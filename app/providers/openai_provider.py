"""OpenAIProvider — stub for future use.

Not implemented in Phase 3. All agents use ClaudeProvider in production.

This stub satisfies the LLMClient interface so that:
  - The provider abstraction is demonstrably complete
  - Future OpenAI integration requires changes only to this file
  - Agent code never needs to change when switching providers
"""

from app.providers.llm_client import LLMClient


class OpenAIProvider(LLMClient):
    """Placeholder OpenAI provider — raises NotImplementedError on all calls.

    To implement: replace each method body with OpenAI SDK calls.
    The interface contract (complete/count_tokens/estimate_cost) must be preserved.
    """

    def complete(self, agent_name: str, context: dict, schema: type) -> dict:
        raise NotImplementedError(
            "OpenAIProvider is not implemented. Use ClaudeProvider."
        )

    def count_tokens(self, text: str) -> int:
        raise NotImplementedError(
            "OpenAIProvider is not implemented. Use ClaudeProvider."
        )

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        raise NotImplementedError(
            "OpenAIProvider is not implemented. Use ClaudeProvider."
        )

    def last_call_usage(self) -> tuple[int, int, float]:
        raise NotImplementedError(
            "OpenAIProvider is not implemented. Use ClaudeProvider."
        )
