    # ADR-024: Track Prompt Versions

    ## Status
    Accepted

    ## Context
    Prompt changes can significantly affect output quality.

    ## Decision
    Every LLM call should record prompt version.

    ## Rationale
    Allows investigation of regressions and quality changes.

    ## Consequences

    ### Positive
    - Better prompt debugging
- Supports prompt evaluation

    ### Tradeoffs
    - Requires prompt metadata discipline

    ## Implementation Notes
    - Use prompt_version fields in llm_calls or agent_events
