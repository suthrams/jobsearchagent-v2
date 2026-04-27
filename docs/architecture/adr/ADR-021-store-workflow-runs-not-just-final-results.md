    # ADR-021: Store Workflow Runs, Not Just Final Results

    ## Status
    Accepted

    ## Context
    v2 is a workflow system with multiple agents, loops, pauses, and metrics.

    ## Decision
    Persist workflow runs, review rounds, human decisions, agent events, LLM calls, metrics, and final outputs.

    ## Rationale
    Debugging multi-step workflows requires more than final results.

    ## Consequences

    ### Positive
    - Traceability
- Better debugging
- Run history
- Future analytics

    ### Tradeoffs
    - More database tables
- More storage

    ## Implementation Notes
    - Add workflow_runs, review_rounds, resume_reviews, human_decisions, run_metrics, agent_events, llm_calls
