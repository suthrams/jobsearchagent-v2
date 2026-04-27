    # ADR-035: Enforce a Structured Workflow State Schema

    ## Status
    Accepted

    ## Context
    Shared state can become a dumping ground if not constrained.

    ## Decision
    All workflows must use a defined state schema. Agents may only read/write allowed fields.

    ## Rationale
    Prevents state drift and downstream bugs.

    ## Consequences

    ### Positive
    - Predictable state
- Easier debugging
- Better testing

    ### Tradeoffs
    - More upfront schema design

    ## Implementation Notes
    - Define WorkflowState schema centrally
- Each agent declares required inputs and produced outputs
