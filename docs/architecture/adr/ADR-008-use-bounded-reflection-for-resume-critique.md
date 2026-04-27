    # ADR-008: Use Bounded Reflection for Resume Critique

    ## Status
    Accepted

    ## Context
    The Resume Critic output should be high quality and not generic. A reviewer loop can improve quality, but unbounded loops are risky.

    ## Decision
    Use a bounded Resume Critic → Review Auditor → Reflection Controller loop.

    ## Rationale
    Reflection improves critique quality while explicit limits prevent infinite loops and cost spikes.

    ## Consequences

    ### Positive
    - Higher quality reviews
- Traceable improvement by round
- Better final report quality

    ### Tradeoffs
    - Additional LLM calls
- More workflow complexity

    ## Implementation Notes
    - Use MAX_REVIEW_ROUNDS = 3 for MVP
- Stop when audit_score >= 85 and confidence >= 85, max rounds reached, or stagnation detected
