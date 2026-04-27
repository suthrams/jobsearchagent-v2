    # ADR-009: Do Not Use Formal Multi-Agent Protocol for MVP

    ## Status
    Accepted

    ## Context
    The system is multi-agent, but does not need direct peer-to-peer agent negotiation.

    ## Decision
    Do not use a formal multi-agent protocol in the MVP. Use workflow-driven orchestration with shared state.

    ## Rationale
    The system needs predictability, debugging, cost control, and HITL checkpoints more than agent autonomy.

    ## Consequences

    ### Positive
    - Simpler execution
- Better traceability
- Lower risk of emergent behavior

    ### Tradeoffs
    - Less autonomous collaboration between agents

    ## Implementation Notes
    - Agents communicate through state and orchestrator only
