    # ADR-016: Add Fidelity Reviewer After Tailoring Agent

    ## Status
    Accepted

    ## Context
    Tailoring output should be validated before being accepted as final.

    ## Decision
    Add a Fidelity Reviewer step after Tailoring Agent.

    ## Rationale
    A second pass catches unsupported claims, inflated metrics, and fabricated experience.

    ## Consequences

    ### Positive
    - Lower hallucination risk
- Better trust
- Clear unsupported-claim detection

    ### Tradeoffs
    - Extra processing step

    ## Implementation Notes
    - Fidelity Reviewer flags invented metrics, unsupported technologies, inflated scope, new certifications, and unsupported domains
