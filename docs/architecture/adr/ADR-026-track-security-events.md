    # ADR-026: Track Security Events

    ## Status
    Accepted

    ## Context
    Security-relevant events should be visible and auditable.

    ## Decision
    Log security events such as prompt injection warnings, blocked tool calls, PII redaction, schema failures, unsupported claims, and cost limits.

    ## Rationale
    Security monitoring improves trust and operational safety.

    ## Consequences

    ### Positive
    - Better auditability
- Easier debugging of safety events

    ### Tradeoffs
    - Additional logging volume

    ## Implementation Notes
    - Add security_events table or event type in agent_events
