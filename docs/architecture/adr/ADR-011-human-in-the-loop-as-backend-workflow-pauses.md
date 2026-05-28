    # ADR-011: Human-in-the-Loop as Backend Workflow Pauses

    ## Status
    Superseded by ADR-059 (Retire In-Graph HITL; Add a Human Edit Decision).

    ADR-011 framed HITL as in-graph backend pauses (`interrupt()` nodes that the
    UI submits decisions against). ADR-059 retires the in-graph interrupt path
    in full and replaces it with two patterns: auto-selection at the job-selection
    step (no human gate) and out-of-graph approvals for tailoring (curate-after
    via a separate REST endpoint, ADR-055). The original principle — backend
    owns orchestration, human controls consequential decisions — is preserved;
    the mechanism is no longer a graph pause.

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
