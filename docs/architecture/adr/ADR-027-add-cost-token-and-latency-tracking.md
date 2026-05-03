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
    - Extended by ADR-053: per-agent / per-model rollup is surfaced in both the
      generated markdown report and the Workflow Detail UI (provider, model,
      calls, tokens in/out, cost, average latency, plus an aggregate row).
      Drives the user's per-agent provider/model selection decisions.
