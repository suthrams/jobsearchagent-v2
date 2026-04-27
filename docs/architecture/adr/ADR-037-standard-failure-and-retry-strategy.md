    # ADR-037: Standard Failure and Retry Strategy

    ## Status
    Accepted

    ## Context
    LLM calls, tool calls, and scraping can fail.

    ## Decision
    Use a standard retry and fallback policy for agents and tools.

    ## Rationale
    Prevents silent failures and inconsistent error handling.

    ## Consequences

    ### Positive
    - Better reliability
- Predictable failure behavior

    ### Tradeoffs
    - Additional error-handling code

    ## Implementation Notes
    - Retry LLM calls once
- Retry schema repair once
- Fail safely with clear error state
