    # ADR-044: Define v1 to v2 Migration Strategy

    ## Status
    Accepted

    ## Context
    v2 should reuse useful v1 plumbing without blindly copying old structure.

    ## Decision
    Migrate feature by feature from v1 into clean v2 layers.

    ## Rationale
    Controlled migration lowers risk and avoids a big-bang rewrite.

    ## Consequences

    ### Positive
    - Safer migration
- Better learning path
- Clear comparison to v1

    ### Tradeoffs
    - Temporary duplication

    ## Implementation Notes
    - Start with resume parsing, job scraping, scoring, then add deep review workflow
