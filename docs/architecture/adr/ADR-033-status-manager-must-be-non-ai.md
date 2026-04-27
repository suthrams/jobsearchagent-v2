    # ADR-033: Status Manager Must Be Non-AI

    ## Status
    Accepted

    ## Context
    Application status updates are deterministic and consequential.

    ## Decision
    Status Manager must be deterministic service logic, not an LLM agent.

    ## Rationale
    Prevents accidental or incorrect workflow/application status changes.

    ## Consequences

    ### Positive
    - Reliable state changes
- Auditable behavior

    ### Tradeoffs
    - Less natural-language flexibility

    ## Implementation Notes
    - Status updates require explicit inputs and validation
