    # ADR-007: Use Structured Output Schemas

    ## Status
    Accepted

    ## Context
    Free-form LLM responses are hard to persist, display, test, and validate.

    ## Decision
    All important LLM outputs must use structured schemas such as Pydantic models or JSON contracts.

    ## Rationale
    Structured outputs improve reliability, UI integration, database persistence, and regression testing.

    ## Consequences

    ### Positive
    - Predictable outputs
- Validation before persistence
- Better UI rendering
- Easier testing

    ### Tradeoffs
    - More upfront schema design
- Retries may be needed when schema validation fails

    ## Implementation Notes
    - Define schemas for JobScore, ResumeReview, ReviewAudit, CareerAdvice, InterviewPrep, TailoredBullet, HumanDecision, RunMetrics
