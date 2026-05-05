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
    - As of ADR-056, the reviewer also enforces the per-bullet length band (0.85x..1.05x of original word count), validates section_label against the candidate's actual resume sections, and rejects claim_type="remove" with non-empty suggested_text. Layout violations land in required_revisions

    ## References
    - ADR-056 — Tailoring Page-Budget Contract and Section-Grouped Suggestions (extends this ADR; does not supersede it)
