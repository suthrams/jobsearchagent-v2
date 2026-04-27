    # ADR-018: Global Ethics Guardrails Must Be Included in Agent Prompts

    ## Status
    Accepted

    ## Context
    Ethical principles must be enforced in prompts, not just documented.

    ## Decision
    Every agent prompt must include shared ethics guardrails.

    ## Rationale
    Prompt-level enforcement makes truthfulness, privacy, and constructive guidance part of runtime behavior.

    ## Consequences

    ### Positive
    - Consistent behavior across agents
- Lower hallucination risk
- Better tone control

    ### Tradeoffs
    - Longer prompts

    ## Implementation Notes
    - Create prompts/shared/ethics_guardrails.md
- Inject it into all agent prompts
