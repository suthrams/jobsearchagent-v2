    # ADR-027: Add Cost, Token, and Latency Tracking

    ## Status
    Accepted

    ## Context
    LLM workflows can become expensive and slow.

    ## Decision
    Track tokens, cost, latency, model, provider, and LLM call count.

    ## Rationale
    Cost and performance must be visible to tune the system.

    ## Consequences

    ### Positive
    - Cost control
- Performance visibility
- Better model comparison

    ### Tradeoffs
    - Requires provider metadata capture

    ## Implementation Notes
    - Track per call and summarize per workflow
