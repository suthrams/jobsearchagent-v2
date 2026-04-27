    # ADR-041: All Agent Execution Must Be Bounded

    ## Status
    Accepted

    ## Context
    Agent loops and tool calls can become expensive or unpredictable.

    ## Decision
    All loops, tool calls, and workflows must have explicit limits.

    ## Rationale
    Prevents runaway behavior and cost spikes.

    ## Consequences

    ### Positive
    - Cost control
- Predictable runtime
- Safer execution

    ### Tradeoffs
    - Some outputs may stop before ideal quality

    ## Implementation Notes
    - Set max research steps, max review rounds, max LLM calls, max cost per run
