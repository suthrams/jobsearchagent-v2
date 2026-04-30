"""Phase 4 agents — all 8 LangChain reasoning agents.

Every agent follows the same pattern:
  __init__(provider: LLMClient, observability: ObservabilityService)
  run(workflow_id: str, context: dict) -> PydanticSchema

Import directly from individual modules to keep dependency graphs shallow.
"""
