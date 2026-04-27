    # ADR-010: Use ReAct Selectively in Research Agent Only

    ## Status
    Accepted

    ## Context
    ReAct is useful for dynamic tool-assisted research, but using it everywhere would increase complexity.

    ## Decision
    Use ReAct only inside the Research Agent.

    ## Rationale
    Research may require tool calls and observations. Scoring, critique, auditing, and advising should remain structured and predictable.

    ## Consequences

    ### Positive
    - Adds genuine agentic behavior
- Keeps complexity bounded
- Improves job/company context

    ### Tradeoffs
    - Research Agent becomes slightly more complex

    ## Implementation Notes
    - Set MAX_RESEARCH_STEPS = 2
- Do not use ReAct in Scoring, Critic, Auditor, Career Advisor, or Tailoring
