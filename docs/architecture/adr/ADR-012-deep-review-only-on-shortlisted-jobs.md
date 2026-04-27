    # ADR-012: Deep Review Only on Shortlisted Jobs

    ## Status
    Accepted

    ## Context
    Deep review requires multiple LLM calls and can become expensive if run on every job.

    ## Decision
    Run expensive critic/advisor workflows only on shortlisted jobs selected by threshold or user approval.

    ## Rationale
    This controls cost and keeps deep analysis focused on promising opportunities.

    ## Consequences

    ### Positive
    - Lower cost
- Better user focus
- Faster runs

    ### Tradeoffs
    - Some low-scoring jobs may not receive deeper analysis

    ## Implementation Notes
    - Use match score threshold or explicit user approval before deep review
