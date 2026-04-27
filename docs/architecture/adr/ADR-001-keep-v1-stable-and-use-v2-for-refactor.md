    # ADR-001: Keep v1 Stable and Use v2 for Refactor

    ## Status
    Accepted

    ## Context
    The existing jobsearchagent v1 already contains working functionality for resume parsing, job scraping, scoring, persistence, dashboarding, and tailoring. v2 will introduce deeper architectural changes, including specialized agents, structured outputs, reflection loops, observability, ethics, and future LangGraph orchestration.

    ## Decision
    Keep v1 stable and use jobsearchagent-v2 as the refactor and learning branch.

    ## Rationale
    This avoids breaking a working baseline while allowing v2 to evolve safely. It also provides a reference implementation for comparison during migration.

    ## Consequences

    ### Positive
    - v1 remains usable
- v2 can evolve safely
- Migration can happen feature by feature

    ### Tradeoffs
    - Temporary duplication between v1 and v2
- Some effort required to keep architectural intent clear

    ## Implementation Notes
    - Do not make major architectural changes in v1
- Use v2 for new architecture, agent workflows, and experiments
- Reference v1 behavior when migrating features
