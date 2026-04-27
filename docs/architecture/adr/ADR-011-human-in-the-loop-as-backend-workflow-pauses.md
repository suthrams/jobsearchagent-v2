    # ADR-011: Human-in-the-Loop as Backend Workflow Pauses

    ## Status
    Accepted

    ## Context
    The user must remain in control of consequential decisions, but the UI should not orchestrate the workflow.

    ## Decision
    Represent HITL as backend workflow pauses. Backend emits a decision request. UI displays it. User submits decision. Backend resumes.

    ## Rationale
    This keeps user control while preserving backend ownership of orchestration.

    ## Consequences

    ### Positive
    - Clean HITL model
- Testable decisions
- Workflow can resume safely
- UI remains thin

    ### Tradeoffs
    - Requires workflow state persistence
- Requires decision endpoints later

    ## Implementation Notes
    - Use status waiting_for_user
- Persist pending decision and user response
