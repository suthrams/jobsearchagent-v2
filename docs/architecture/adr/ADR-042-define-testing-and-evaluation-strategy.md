    # ADR-042: Define Testing and Evaluation Strategy

    ## Status
    Accepted

    ## Context
    Agentic workflows require tests beyond simple unit tests.

    ## Decision
    Create unit tests for services/tools/schemas and integration tests for workflows.

    ## Rationale
    Testing is needed to prevent regressions as agents and prompts evolve.

    ## Consequences

    ### Positive
    - Higher confidence
- Safer refactoring
- Better workflow reliability

    ### Tradeoffs
    - More setup effort

    ## Implementation Notes
    - Add fixtures for resume/job examples
- Test schema validation, stop logic, and workflow transitions
