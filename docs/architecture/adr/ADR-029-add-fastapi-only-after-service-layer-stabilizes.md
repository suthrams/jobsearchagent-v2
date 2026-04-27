    # ADR-029: Add FastAPI Only After Service Layer Stabilizes

    ## Status
    Accepted

    ## Context
    Adding FastAPI too early can lock in unstable workflow contracts.

    ## Decision
    Add FastAPI after backend service and workflow boundaries are stable.

    ## Rationale
    API design should reflect proven workflow services.

    ## Consequences

    ### Positive
    - Avoids premature API design
- Cleaner endpoints later

    ### Tradeoffs
    - Initial UI may call services directly

    ## Implementation Notes
    - Build workflow service functions first, then expose them through FastAPI
