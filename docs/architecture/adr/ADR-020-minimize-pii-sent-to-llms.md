    # ADR-020: Minimize PII Sent to LLMs

    ## Status
    Accepted

    ## Context
    Resumes contain personal identifiable information.

    ## Decision
    Send parsed/redacted profiles to agents where possible instead of raw resume PII.

    ## Rationale
    Reduces privacy exposure and unnecessary data sharing.

    ## Consequences

    ### Positive
    - Better privacy posture
- Less sensitive logging risk

    ### Tradeoffs
    - Requires parsing/redaction step

    ## Implementation Notes
    - Avoid sending phone, email, address unless required
- Do not log raw resume text
