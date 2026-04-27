    # ADR-023: Make Observability First-Class

    ## Status
    Accepted

    ## Context
    Multi-agent workflows are harder to debug than v1's simple pipeline.

    ## Decision
    Track workflow status, agent events, LLM calls, tool calls, review rounds, decisions, errors, costs, latency, and tokens.

    ## Rationale
    Observability is required for reliability, trust, and debugging.

    ## Consequences

    ### Positive
    - Clear run timeline
- Cost visibility
- Better debugging
- Quality tracking

    ### Tradeoffs
    - More logging and tables

    ## Implementation Notes
    - Add ObservabilityService
- Log events at workflow, agent, LLM, tool, and decision levels
