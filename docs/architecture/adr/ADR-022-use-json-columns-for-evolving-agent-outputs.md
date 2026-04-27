    # ADR-022: Use JSON Columns for Evolving Agent Outputs

    ## Status
    Accepted

    ## Context
    Agent output schemas will evolve during v2 development.

    ## Decision
    Store rich agent outputs as JSON text columns initially.

    ## Rationale
    This preserves flexibility while schemas stabilize.

    ## Consequences

    ### Positive
    - Flexible persistence
- Faster iteration

    ### Tradeoffs
    - Less queryable than normalized fields

    ## Implementation Notes
    - Use final_review_json, audit_output_json, advice_json, prep_json, state_json
