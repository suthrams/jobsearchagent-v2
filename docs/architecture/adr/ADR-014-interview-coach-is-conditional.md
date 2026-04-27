    # ADR-014: Interview Coach Is Conditional

    ## Status
    Accepted

    ## Context
    Interview preparation is only useful when a role is promising or the user explicitly asks for it.

    ## Decision
    Run Interview Coach only when match_score >= 85 or when explicitly requested by the user.

    ## Rationale
    This avoids unnecessary cost and keeps the workflow focused.

    ## Consequences

    ### Positive
    - Lower cost
- More relevant prep
- Simpler MVP

    ### Tradeoffs
    - Some medium-fit jobs may not get prep unless requested

    ## Implementation Notes
    - Add conditional execution after Career Advisor
