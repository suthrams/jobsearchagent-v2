    # ADR-015: Tailoring Must Be Evidence-Bound

    ## Status
    Accepted

    ## Context
    Resume tailoring is the highest hallucination-risk area.

    ## Decision
    Every tailored claim must map to supporting evidence from the original resume or parsed profile.

    ## Rationale
    Prevents fabricated accomplishments, metrics, technologies, titles, and leadership scope.

    ## Consequences

    ### Positive
    - Improves trust
- Reduces ethical risk
- Prevents misleading resumes

    ### Tradeoffs
    - Tailored output may be more conservative

    ## Implementation Notes
    - Each tailored bullet must include supporting_evidence, claim_type, fidelity_risk, and unsupported_claims
