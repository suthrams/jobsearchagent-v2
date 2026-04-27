    # ADR-028: Start with Streamlit and SQLite MVP

    ## Status
    Accepted

    ## Context
    The first goal is proving core intelligence, not building production infrastructure.

    ## Decision
    Use Streamlit and SQLite for the MVP.

    ## Rationale
    This keeps development simple and focused.

    ## Consequences

    ### Positive
    - Fast iteration
- Low infrastructure burden
- Good for local/demo usage

    ### Tradeoffs
    - Not ideal for multi-user scaling

    ## Implementation Notes
    - Defer FastAPI, Postgres, Redis, React, background workers, and multi-user deployment
