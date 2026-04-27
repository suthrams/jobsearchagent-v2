    # ADR-034: Do Not Overbuild Before Proving Core Workflow

    ## Status
    Accepted

    ## Context
    The design includes many future-state features.

    ## Decision
    Do not build the full platform before proving the core deep-review workflow.

    ## Rationale
    The core value is analysis quality, not infrastructure size.

    ## Consequences

    ### Positive
    - Faster validation
- Lower complexity
- Less wasted work

    ### Tradeoffs
    - Some architecture features deferred

    ## Implementation Notes
    - MVP is resume + one job → score → critic/auditor → career advice → report
