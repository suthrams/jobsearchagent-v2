    # ADR-036: Define Explicit Agent Input and Output Contracts

    ## Status
    Accepted

    ## Context
    Agents need clear interfaces to avoid brittle downstream dependencies.

    ## Decision
    Each agent must define input and output schemas.

    ## Rationale
    Reliable orchestration requires stable contracts.

    ## Consequences

    ### Positive
    - Fewer integration bugs
- Better validation
- Cleaner tests

    ### Tradeoffs
    - More schema work

    ## Implementation Notes
    - No free-form text-only outputs for important agent steps
