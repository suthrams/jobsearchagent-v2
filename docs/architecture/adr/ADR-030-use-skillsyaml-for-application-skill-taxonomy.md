    # ADR-030: Use skills.yaml for Application Skill Taxonomy

    ## Status
    Accepted

    ## Context
    The application needs a machine-readable skill taxonomy for normalization, scoring, and gap analysis.

    ## Decision
    Use data/skills.yaml as the canonical application skill taxonomy.

    ## Rationale
    YAML supports aliases, categories, weights, and relationships better than Markdown.

    ## Consequences

    ### Positive
    - Better skill normalization
- Improved scoring
- Better gap analysis

    ### Tradeoffs
    - Taxonomy maintenance required

    ## Implementation Notes
    - Do not confuse data/skills.yaml with Claude Code .claude/skills
