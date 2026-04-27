    # ADR-004: Backend Owns Workflow Orchestration

    ## Status
    Accepted

    ## Context
    The UI should not decide which agent runs next or how state transitions occur.

    ## Decision
    Backend services or LangGraph workflows will own workflow orchestration.

    ## Rationale
    Workflow routing, retries, reflection loops, state transitions, and persistence belong in a testable backend layer.

    ## Consequences

    ### Positive
    - Deterministic workflows
- Better testing
- Cleaner observability
- Easier future API exposure

    ### Tradeoffs
    - UI becomes dependent on backend workflow APIs

    ## Implementation Notes
    - Create workflow services before adding FastAPI
- Do not put orchestration logic in Streamlit session state
