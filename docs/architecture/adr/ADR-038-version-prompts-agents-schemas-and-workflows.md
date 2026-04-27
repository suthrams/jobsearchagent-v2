    # ADR-038: Version Prompts, Agents, Schemas, and Workflows

    ## Status
    Accepted

    ## Context
    Changes to prompts, schemas, and workflows can alter behavior.

    ## Decision
    Version prompts, agents, schemas, and workflows.

    ## Rationale
    Versioning enables regression analysis and reproducibility.

    ## Consequences

    ### Positive
    - Better debugging
- Supports experiments
- Clear migration path

    ### Tradeoffs
    - Metadata discipline required

    ## Implementation Notes
    - Persist prompt_version, agent_version, schema_version, workflow_version
