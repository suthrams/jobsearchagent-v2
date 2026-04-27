    # ADR-006: Keep Deterministic Work in Tools and Services

    ## Status
    Accepted

    ## Context
    Not every operation should be handled by an LLM agent.

    ## Decision
    PDF parsing, job scraping, database writes, status updates, skill normalization, and report generation should be deterministic tools/services.

    ## Rationale
    LLMs should reason. Services and tools should execute predictable operations.

    ## Consequences

    ### Positive
    - Improves reliability
- Reduces hallucination risk
- Improves testability
- Simplifies security controls

    ### Tradeoffs
    - Requires more regular application code

    ## Implementation Notes
    - Agents request tool actions through approved interfaces
- Tools validate input and log execution
