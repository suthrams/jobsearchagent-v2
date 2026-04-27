    # ADR-013: Separate Resume Gaps from Career Gaps

    ## Status
    Accepted

    ## Context
    A missing job requirement is not always something that should be rewritten into the resume.

    ## Decision
    Resume Critic identifies presentation gaps. Career Advisor identifies actual capability, proof-point, or positioning gaps.

    ## Rationale
    This prevents the system from fabricating resume content to cover real gaps.

    ## Consequences

    ### Positive
    - More truthful advice
- Better career strategy
- Lower hallucination risk

    ### Tradeoffs
    - Requires separate advisor output

    ## Implementation Notes
    - Resume gap means messaging issue
- Career gap means actual skill/proof-point gap
