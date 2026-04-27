    # ADR-031: Separate Claude Code Support Files from App Code

    ## Status
    Accepted

    ## Context
    Claude Code support files serve the coding assistant, not the runtime application.

    ## Decision
    Keep CLAUDE.md, .claude/agents, .claude/skills, .claude/commands, and .claude/hooks separate from app code.

    ## Rationale
    Avoids confusing assistant guidance with application runtime assets.

    ## Consequences

    ### Positive
    - Cleaner repo organization
- Better Claude Code usability

    ### Tradeoffs
    - More project structure

    ## Implementation Notes
    - Use data/skills.yaml for app taxonomy and .claude/skills/*/SKILL.md for Claude Code skills
