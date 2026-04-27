    # ADR-017: Ethical AI Use for Career Decision Support

    ## Status
    Accepted

    ## Context
    The system influences real career decisions and must be treated as decision support, not an authority.

    ## Decision
    Prioritize truthfulness, autonomy, fairness, privacy, constructive tone, and transparency.

    ## Rationale
    Career tooling can impact confidence, opportunities, and representation. Ethical guardrails improve trust and safety.

    ## Consequences

    ### Positive
    - Higher trust
- Better user experience
- Reduced risk of harmful advice

    ### Tradeoffs
    - More conservative outputs
- More validation required

    ## Implementation Notes
    - Do not fabricate
- Keep humans in control
- Distinguish resume gaps from career gaps
- Show reasoning and confidence
