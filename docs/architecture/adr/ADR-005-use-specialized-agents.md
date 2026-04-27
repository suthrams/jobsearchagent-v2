    # ADR-005: Use Specialized Agents

    ## Status
    Accepted

    ## Context
    A single general-purpose agent would become too broad and difficult to evaluate.

    ## Decision
    Use specialized agents for scoring, research, resume critique, review audit, career advice, interview prep, and tailoring.

    ## Rationale
    Specialized agents are easier to prompt, test, evaluate, and improve.

    ## Consequences

    ### Positive
    - Better output quality
- Clearer responsibilities
- Easier testing
- Less prompt complexity

    ### Tradeoffs
    - More files and schemas
- More orchestration required

    ## Implementation Notes
    - Create agent modules only as features are implemented
- Each agent must have explicit input/output contracts
