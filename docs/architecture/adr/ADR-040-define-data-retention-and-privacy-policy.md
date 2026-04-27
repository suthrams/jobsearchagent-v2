    # ADR-040: Define Data Retention and Privacy Policy

    ## Status
    Accepted

    ## Context
    Resume data is sensitive and should not be stored indefinitely without intent.

    ## Decision
    Store user data only as long as necessary and support deletion.

    ## Rationale
    Reduces privacy risk and improves user trust.

    ## Consequences

    ### Positive
    - Stronger privacy posture
- Lower risk exposure

    ### Tradeoffs
    - Requires lifecycle logic later

    ## Implementation Notes
    - Avoid indefinite raw resume storage
- Support delete operations
- Prefer parsed/redacted profile for agent use
