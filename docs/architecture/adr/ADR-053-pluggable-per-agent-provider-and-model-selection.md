# ADR-053: Pluggable Per-Agent Provider and Model Selection

## Status

Accepted (supersedes the static per-agent assignment of ADR-051; amends the
"users cannot modify LLM models" constraint of ADR-046)

## Context

The original v2 design (ADR-051) hard-coded a 2-tier Claude model assignment:
Haiku for high-volume / validation agents, Sonnet for generative / advisory
agents. ADR-046 reinforced this by listing `LLM models` among the keys that
users cannot override. ADR-032 declared a provider abstraction but in practice
only `ClaudeProvider` was implemented; `OpenAIProvider` was a stub that raised
`NotImplementedError` on every method.

Two operational realities have made this rigid setup untenable:

1. **Provider rate limits.** Sustained development against Claude (Pro and
   Pro Max plans) repeatedly produced `429` responses on the agents that fan
   out per job (`research_agent`, `scoring_agent`). Even with the 6-attempt
   retry-after-aware backoff added in the v2 usability refactor, a user
   running multiple workflows back-to-back can be blocked for minutes at a
   time. There is no escape hatch to a different provider.
2. **Cost ceiling per provider.** Each provider has its own pricing curve.
   When a user wants to run many low-stakes scoring passes cheaply but keep
   high-quality reasoning for tailoring, the only knob today is the global
   model — there is no per-agent flexibility, and no cross-provider option at
   all.

We need (a) a real second provider, (b) per-agent provider + model
selection, and (c) cost reporting that is granular enough to inform those
choices.

## Decision

1. **Implement `OpenAIProvider` for real**, satisfying the same `LLMClient`
   contract as `ClaudeProvider`. Supports `gpt-4o`, `gpt-4o-mini`, and `o1`
   on day one; new models added by extending the pricing table only.
2. **Introduce a `ModelRegistry`** at startup that holds one `LLMClient`
   instance per unique `(provider, model)` pair. Agents are wired through
   the registry rather than being given a single provider directly.
3. **Make per-agent model assignment user-configurable** via a new `agents.*`
   block in the merged configuration:
   ```yaml
   agents:
     research_agent:    {provider: claude, model: claude-haiku-4-5-20251001}
     scoring_agent:     {provider: claude, model: claude-haiku-4-5-20251001}
     resume_critic:     {provider: claude, model: claude-sonnet-4-6}
     career_advisor:    {provider: openai, model: gpt-4o}
     interview_coach:   {provider: claude, model: claude-sonnet-4-6}
     tailoring_agent:   {provider: claude, model: claude-sonnet-4-6}
     review_auditor:    {provider: claude, model: claude-haiku-4-5-20251001}
     fidelity_reviewer: {provider: claude, model: claude-haiku-4-5-20251001}
   ```
   Defaults match ADR-051. User overrides go through `user_config` and the
   existing `ConfigService` merge layer.
4. **Restart-to-apply.** Per-run override is deferred. Switching an agent's
   model requires saving via the Settings UI and restarting the backend.
   Workflows in flight continue with the assignment they started under.
5. **Surface per-agent cost rollups** in both the markdown run report and the
   Workflow Detail UI. Each row shows `provider · model · calls · in tokens ·
   out tokens · cost · avg latency`, with an aggregate row at the bottom.
   This is the feedback loop that makes the provider/model picker actionable.

## Constraints (replacing the relevant clause of ADR-046)

The "Users cannot modify: LLM models" constraint in ADR-046 is **narrowed**:

- Users **may** select an agent's `provider` and `model`, **but only from
  values registered in `ModelRegistry`** at startup. Arbitrary model strings
  are rejected.
- The list of registered models is sourced from a single registry definition
  in `app/providers/model_registry.py` — adding a model requires a code
  change (and therefore a code review + cost-table update).
- Prompt definitions, safety limits, retention windows, and execution caps
  remain immutable per ADR-046.
- Per-agent model selection is **not** considered safety-sensitive — the
  guardrails (`prompts/shared/guardrails.txt`) apply identically regardless
  of provider, and structured-output validation on the agent side rejects
  malformed responses from any model.

## Rationale

- **Reliability.** Routing one or two agents to OpenAI gives the user a
  working escape hatch when Claude rate-limits. Without this, a 429 storm
  blocks the whole workflow.
- **Cost flexibility.** Different providers have different pricing
  inflection points. `gpt-4o-mini` is cheaper than Haiku on small contexts;
  `o1` is more expensive but stronger on reasoning-heavy tailoring. The user
  picks per agent.
- **Visibility drives the decision.** Per-step cost reporting (extension of
  ADR-027) makes the trade-offs explicit — the user sees in dollars where
  each agent is spending and can rebalance.
- **Risk is bounded.** Models must come from the registry, so a user cannot
  point an agent at an unknown provider, an unsupported model, or a model
  that won't honor structured-output requests.

## Consequences

### Positive

- Workflows survive single-provider rate limit and outage windows.
- Per-agent model selection unlocks true cost optimisation (vs the all-or-nothing
  knob today).
- ADR-032's promise of provider flexibility is finally real — `OpenAIProvider`
  goes from stub to first-class.
- Per-step cost rollup lets users see exactly which agent consumed which budget.

### Tradeoffs

- Two provider implementations to maintain. Differences in structured-output
  semantics (Anthropic's tool-use vs OpenAI's `response_format=json_schema`),
  error types for retry, and token-counting must be encapsulated in the
  provider — agents stay provider-agnostic.
- Cost-table drift risk: pricing changes upstream don't automatically reach
  our hardcoded tables. Mitigated by the cost-rollup view making
  inaccuracies visible.
- Restart-to-apply UX. Acceptable for a single-user app; revisit if multi-run
  experimentation becomes important.

## Implementation Notes

- `app/providers/model_registry.py`: `ModelRegistry` class. Constructor
  builds providers eagerly for every `(provider, model)` referenced by the
  effective config; exposes `for_agent(agent_name) -> LLMClient`.
- `app/providers/openai_provider.py`: replace stub with real implementation
  using `openai` SDK + `response_format={"type": "json_schema", ...}`.
  Pricing table per ADR-053. Same retry-after-aware backoff as
  `ClaudeProvider`.
- `app/api/dependencies.py`: build `ModelRegistry` once after config load;
  pass each agent its resolved provider.
- `app/services/config_service.py`: `_PROTECTED_KEYS` removes the absolute
  ban on model keys; instead, validation against `ModelRegistry`'s known set
  enforces correctness. Other model-related keys (e.g. `llm.default_model`)
  remain protected because they are no longer used.
- `app/services/cost_breakdown.py`: pure function over `llm_calls` rows
  returning `[{agent_name, provider, model, calls, tokens_in, tokens_out,
  cost, avg_latency_ms}, ..., aggregate]`.
- `app/services/report_generator.py`: new "Cost Breakdown" section in the
  generated markdown report.
- `app/ui/streamlit_app.py`: Settings screen exposes a per-agent dropdown
  (provider → model, with per-1K-call cost shown) backed by the registry.
  Workflow Detail and the Run Report show the cost breakdown table.


## Addendum 2026-05-05 — Live reload supersedes restart for runtime overrides

The original ADR said "Saving a change requires a backend restart to take
effect." That friction caused real cost waste: on 2026-05-06 a $0.52 run
landed because the user had saved `scoring_agent: Haiku` to user_config
but the backend was still running with the previous Sonnet binding.

The fix is `POST /config/reload` (`app/api/routers/config.py`). It calls
`reload_deps_and_graph()` (`app/api/dependencies.py`) which:

  1. Snapshots the old SqliteSaver cleanup function.
  2. Runs the same build path startup uses (`build_and_cache_graph`),
     which re-reads `user_config` and rebuilds `ModelRegistry`, all
     agents, and the compiled graph.
  3. Releases the old SqliteSaver only after the new graph is wired.

The Streamlit Settings page calls `api.reload_config()` after every
successful `put_config(...)`. Toast surfaces the new effective assignment
("Saved + applied. Active: scoring_agent → claude/claude-haiku-4-5-20251001")
so the user can verify the change took effect.

In-flight workflows are not disturbed: their LangGraph runner holds a
reference to the OLD graph + agents and continues using them until the
run completes. Only workflows started AFTER the reload pick up the new
assignment. This matches the semantics of a real restart, just without
process exit.

What `POST /config/reload` does NOT pick up (still requires real restart):

- Prompt file changes — `PromptLoader` caches files at first read.
- Code changes — Python doesn't reload modules.
- Environment variable changes — `os.getenv` reads at process start; if
  `OPENAI_API_KEY` is set AFTER the backend is already running, the
  registry rebuild WILL pick it up correctly because env-var reads happen
  inside `_build_real_deps` rather than at import time.

Tests in `tests/v2/test_api_config.py`:
  - `test_reload_endpoint_returns_status_and_assignment`
  - `test_reload_endpoint_surfaces_rebuild_failure_as_500`
  - `test_reload_endpoint_idempotent`
