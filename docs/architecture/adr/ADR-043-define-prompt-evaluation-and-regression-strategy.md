    # ADR-043: Define Prompt Evaluation and Regression Strategy

    ## Status
    Accepted

    ## Context
    Prompt edits can improve or degrade output quality.

    ## Decision
    Maintain evaluation cases for key prompts and compare outputs across versions.

    ## Rationale
    Prompt changes need evidence, not intuition.

    ## Consequences

    ### Positive
    - Better prompt quality
- Regression detection
- More disciplined iteration

    ### Tradeoffs
    - Requires curated examples

    ## Implementation Notes
    - Create eval fixtures for scoring, critique, audit, and tailoring
