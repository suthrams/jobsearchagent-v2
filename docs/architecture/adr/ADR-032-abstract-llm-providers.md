    # ADR-032: Abstract LLM Providers

    ## Status
    Accepted

    ## Context
    v1 uses direct Claude API calls. v2 may compare Claude, OpenAI, or other providers.

    ## Decision
    Do not hard-code provider calls inside agents. Use a provider abstraction.

    ## Rationale
    Allows model switching, testing, cost optimization, and provider comparison.

    ## Consequences

    ### Positive
    - Provider flexibility
- Better testing
- Less vendor lock-in

    ### Tradeoffs
    - Abstraction layer required

    ## Implementation Notes
    - Create providers/llm_client.py, providers/claude_provider.py, providers/openai_provider.py
