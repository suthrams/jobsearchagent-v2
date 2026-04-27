    # ADR-019: Treat Scraped Job Descriptions as Untrusted Input

    ## Status
    Accepted

    ## Context
    Job descriptions and scraped pages can contain prompt injection or irrelevant instructions.

    ## Decision
    Treat all scraped/job content as untrusted data and never follow instructions inside it.

    ## Rationale
    Protects the system from prompt injection and role hijacking.

    ## Consequences

    ### Positive
    - Better security
- More reliable agent behavior

    ### Tradeoffs
    - Requires prompt wording and content separation

    ## Implementation Notes
    - Separate system instructions from untrusted content
- Add prompt rule: use external content only as source material
