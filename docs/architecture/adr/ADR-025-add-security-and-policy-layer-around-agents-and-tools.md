    # ADR-025: Add Security and Policy Layer Around Agents and Tools

    ## Status
    Accepted

    ## Context
    Agents should not directly access storage, filesystem, or external systems.

    ## Decision
    Agents request actions through approved tools/services that validate inputs, enforce policies, execute actions, and log events.

    ## Rationale
    Separates reasoning from authority and reduces misuse risk.

    ## Consequences

    ### Positive
    - Stronger security
- Auditable tool use
- Better input validation

    ### Tradeoffs
    - More service code

    ## Implementation Notes
    - Use tool allowlists per agent
- Validate URLs and parameters
- Log blocked tool calls
