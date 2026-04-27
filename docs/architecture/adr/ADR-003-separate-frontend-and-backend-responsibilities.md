    # ADR-003: Separate Frontend and Backend Responsibilities

    ## Status
    Accepted

    ## Context
    v1 could keep workflow logic closer to Streamlit because the application was simpler. v2 introduces workflow state, reflection loops, HITL, observability, and security checks.

    ## Decision
    The frontend will act as a thin control surface. The backend/workflow layer will own orchestration and execution.

    ## Rationale
    This keeps Streamlit replaceable, makes workflows testable outside the UI, and supports future FastAPI or React frontends.

    ## Consequences

    ### Positive
    - Cleaner separation of concerns
- Easier testing
- Future UI replacement becomes easier
- Security boundary is clearer

    ### Tradeoffs
    - More upfront structure
- Additional service layer required

    ## Implementation Notes
    - UI uploads resumes, submits job URLs, shows status, collects approvals
- Backend runs agents, handles state, persists results, and resumes workflows
