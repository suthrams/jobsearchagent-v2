    # ADR-002: Orchestrator-Mediated Agent Coordination with Shared State

    ## Status
    Accepted

    ## Context
    v2 will use multiple specialized agents. If agents directly coordinate with each other, workflows become hard to test, debug, and control.

    ## Decision
    Agents will not coordinate directly. A backend workflow orchestrator will run agents, pass shared workflow state, validate outputs, persist results, and decide the next step.

    ## Rationale
    This keeps workflows predictable, testable, observable, and safer than direct agent-to-agent communication.

    ## Consequences

    ### Positive
    - Easier debugging
- Cleaner state transitions
- Better support for HITL pauses
- More reliable execution

    ### Tradeoffs
    - Less agent autonomy
- More orchestration code

    ## Implementation Notes
    - Agents read from state and write structured outputs back to state
- Orchestrator owns routing
- Agents remain mostly stateless
