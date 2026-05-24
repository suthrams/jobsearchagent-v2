# ADR-058: Model catalog, pricing, and default assignment move to YAML; cost-cap stays in code; per-workflow assignment snapshot at kickoff

## Status

Accepted. Supersedes the in-code catalog and default-assignment constants of
ADR-053. Amends ADR-046 (which already accepts `agents.*` as user-editable);
this ADR extends the same path to the model catalog and pricing data.

## Context

ADR-053 introduced per-agent `(provider, model)` selection via `ModelRegistry`.
At the time, three things lived in Python source files:

1. `_KNOWN_MODELS` — the catalog of registered (provider, model) pairs.
2. `_PRICING` — per-model token pricing, one table per provider class.
3. `DEFAULT_AGENT_ASSIGNMENT` — the per-agent (provider, model) default.

ADR-053 explicitly noted "adding a model requires a code change (and
therefore a code review + cost-table update)" as a benefit. Six months of
running the system has surfaced the cost of that decision:

- **Model lifecycle is faster than code releases.** Anthropic and OpenAI
  ship new models, deprecate old ones, and adjust pricing on a cadence the
  system has no business gating behind a Python edit.
- **Cost-table drift.** ADR-053's "tradeoffs" section flagged this risk
  directly. The Cost Dashboard reconciliation work in early May surfaced a
  stale Haiku 4.5 pricing constant that caused a several-hundred-percent
  undercount across two days (ADR-trail: see `docs/incidents/`). Pricing
  in code drifts because nothing reminds the maintainer to update it.
- **The article-five thesis.** The published article argues that flexibility
  belongs in the original design because LLMs evolve faster than codebases
  retrofit. The system's own catalog and pricing being in code contradicts
  that thesis and undermines its credibility.

At the same time, two pieces of model-related code must NOT move:

- The cost-cap allowlist (`HIGH_VOLUME_AGENTS`, `HIGH_VOLUME_SAFE_MODELS`)
  is a policy boundary, not configuration. It exists because a
  configuration-only mechanism failed on 2026-05-09 (a Settings UI slip put
  scoring on Sonnet and the bill jumped). Moving the allowlist to config
  reintroduces the exact failure mode the guardrail was shipped to prevent.
- Provider class dispatch (`if provider == "claude": ClaudeProvider(...)`)
  cannot move to config without a plugin loader, which is out of scope.

A separate ask is per-workflow reproducibility: a workflow run should
record exactly which `(provider, model)` ran each agent, regardless of
config changes that happen after the run starts. And there should be a
hook to override the assignment for a single run without editing
configuration globally.

## Decision

### 1. Move to YAML (config-driven)

- **Model catalog** lives at `models.providers.<provider>[].id` in
  `config/config.yaml`.
- **Pricing** lives alongside each catalog entry as `input_per_m` and
  `output_per_m` (USD per million tokens).
- **Default per-agent assignment** lives at `agents.<agent_name>` with
  `{provider, model}` keys. This already existed for user overrides per
  ADR-053; this ADR formalises it as also the location of defaults.

Adding a model: edit YAML, no Python release. Updating a price: edit YAML,
no Python release.

### 2. Stay in code (policy + dispatch)

- `HIGH_VOLUME_AGENTS` and `HIGH_VOLUME_SAFE_MODELS` constants stay in
  `app/providers/model_registry.py`. The cost-cap enforcement at registry
  build time is unchanged.
- `_FALLBACK_PRICING`, `_CACHE_WRITE_MULTIPLIER`, `_CACHE_READ_MULTIPLIER`
  in `claude_provider.py` stay as mathematical invariants tied to
  Anthropic's documented ephemeral-cache semantics (1.25x / 0.10x). These
  are not pricing data and do not change as new models ship.
- Provider class dispatch stays in `ModelRegistry._build_provider`.

### 3. Validation

- `ConfigService` validates the `models:` block on load via a Pydantic
  schema. A malformed YAML (missing fields, unknown provider, negative
  pricing) fails fast at backend startup. The validator lives in
  `app/services/config_service.py`.
- `ModelRegistry.build()` accepts the validated catalog + assignment from
  ConfigService and enforces:
  - All `(provider, model)` pairs in the assignment exist in the catalog.
  - Any cost-capped agent override outside `HIGH_VOLUME_SAFE_MODELS` is
    snapped to the default (unchanged from current behaviour).

### 4. Per-workflow assignment snapshot

`register_run` writes the effective per-agent assignment into the
workflow's persisted state at run kickoff, under `effective_config.agents`.
This was partially true today via `effective_config` snapshotting; this
ADR formalises that the snapshot MUST include the agents.* block in full,
including agents that fell through to their defaults rather than user
overrides.

Result: any past run can be inspected for "which model ran the advisor on
this workflow," and that answer is stable across later config changes.

### 5. Kickoff-time override hook

The workflow-start API endpoint accepts an optional `agent_overrides` map:

```json
{
  "agent_overrides": {
    "career_advisor":   {"provider": "claude", "model": "claude-opus-4-7"},
    "research_agent":   {"provider": "openai", "model": "gpt-4o-mini"}
  }
}
```

- Validated server-side against the catalog and the cost cap before the
  workflow starts.
- Merged into `effective_config.agents` for this run only.
- Persisted in `workflow_runs.state_json`.

This is the extension point for "I want to try Opus on the advisor for
this one job without editing global config."

### 6. Runtime per-workflow agent swap — Phase 9 follow-up

This ADR commits to the persistence + API surface in Phase 1. **Phase 1
does NOT swap agent providers at runtime** based on per-workflow overrides.
Reason: agents are constructed once at backend startup (see
`_build_real_deps` in `app/api/dependencies.py`) with one `LLMClient`
instance each. Making per-workflow overrides take runtime effect requires
either:

- **Option A:** Lazy provider resolution in `BaseAgent` (each agent
  resolves its provider per-call against the current run's assignment).
  Requires changes to `BaseAgent.run` + all 8 agent implementations + the
  orchestrator nodes that call them.
- **Option B:** Per-run agent and graph rebuild (each workflow with an
  override gets its own `WorkflowDependencies` and compiled graph).
  Expensive at runtime; LangGraph compilation is non-trivial.

A separate ADR will pick between these. Until then, the kickoff override
is recorded in the workflow snapshot but the actual agents fall back to
the global registry assignment. The API endpoint returns a warning in
the response body when overrides are present and not yet active.

## Schema

```yaml
# config/config.yaml (excerpt)

# Per-agent default assignment. User overrides via Settings UI write to
# user_config and merge over these defaults. Cost-capped agents (research,
# scoring) cannot pick a model outside HIGH_VOLUME_SAFE_MODELS — that
# allowlist is enforced in code.
agents:
  research_agent:       {provider: claude, model: claude-haiku-4-5-20251001}
  scoring_agent:        {provider: claude, model: claude-haiku-4-5-20251001}
  resume_critic:        {provider: claude, model: claude-haiku-4-5-20251001}
  review_auditor:       {provider: claude, model: claude-haiku-4-5-20251001}
  fidelity_reviewer:    {provider: claude, model: claude-haiku-4-5-20251001}
  career_advisor:       {provider: claude, model: claude-sonnet-4-6}
  interview_coach:      {provider: claude, model: claude-sonnet-4-6}
  tailoring_agent:      {provider: claude, model: claude-sonnet-4-6}
  resume_parser:        {provider: claude, model: claude-sonnet-4-6}
  custom_url_extractor: {provider: claude, model: claude-sonnet-4-6}

# Model catalog + pricing. Editing this file is how the system learns about
# new models or new prices. No code release required.
models:
  providers:
    claude:
      - {id: claude-haiku-4-5-20251001, input_per_m: 1.00,  output_per_m: 5.00}
      - {id: claude-sonnet-4-6,         input_per_m: 3.00,  output_per_m: 15.00}
      - {id: claude-opus-4-7,           input_per_m: 15.00, output_per_m: 75.00}
    openai:
      # NOTE: OpenAI list as of ADR-053; refresh against the current OpenAI
      # pricing page when this ADR is implemented.
      - {id: gpt-4o-mini, input_per_m: 0.15,  output_per_m: 0.60}
      - {id: gpt-4o,      input_per_m: 2.50,  output_per_m: 10.00}
      - {id: o1,          input_per_m: 15.00, output_per_m: 60.00}
```

## Consequences

### Positive

- Adding a model or updating a price no longer requires a Python release.
  Editing YAML and calling `POST /config/reload` (ADR-053 addendum) is
  enough.
- Per-workflow reproducibility: every run's record shows exactly which
  (provider, model) ran each agent, stable across future config edits.
- The kickoff hook unblocks "I want to try Opus on the advisor for this
  one job" without global config edits.
- The article-five thesis ("design for flexibility from day one") is
  reflected in the system's own configuration model.

### Tradeoffs

- Two locations for model-related data: YAML (catalog + pricing + default
  assignment) and code (cost-cap allowlist + dispatch). The distinction
  must be documented at the top of `config.yaml` and in
  `model_registry.py`. The cost-cap allowlist is a policy invariant, not
  configuration — moving it to YAML reintroduces the failure mode the
  guardrail prevents. This distinction is load-bearing.
- Phase 1 ships persistence + API hook but not runtime swap. Users who
  pass `agent_overrides` will see the override persisted in the run
  record but the agents still use the global registry assignment. The
  endpoint surfaces this with a 200 response containing a `warnings`
  field. Phase 9 closes the gap.

## Implementation Notes

- `app/services/config_service.py` — add a Pydantic schema for the
  `models:` block and the `agents:` block; validate on
  `get_effective_config()`; surface a clear error if invalid.
- `app/providers/model_registry.py` — remove `_KNOWN_MODELS` and
  `DEFAULT_AGENT_ASSIGNMENT` constants. `ModelRegistry.build()` now takes
  the validated catalog + assignment from ConfigService.
  `HIGH_VOLUME_AGENTS` and `HIGH_VOLUME_SAFE_MODELS` stay.
- `app/providers/claude_provider.py` + `app/providers/openai_provider.py`
  — remove `_PRICING`. Accept a `pricing: dict[str, dict[str, float]]`
  constructor argument injected by `ModelRegistry._build_provider`.
  `_FALLBACK_PRICING`, `_CACHE_WRITE_MULTIPLIER`, `_CACHE_READ_MULTIPLIER`
  remain.
- `app/api/dependencies.py` — `_build_real_deps` reads catalog +
  assignment from `ConfigService.get_effective_config()`, passes both to
  `ModelRegistry.build()`.
- `app/repositories/workflow_repository.py` (or `register_run` graph
  node) — ensure `effective_config.agents` snapshot is complete (defaults
  filled in) before persisting.
- `app/api/routers/workflows.py` — extend the workflow-start endpoint
  request schema to accept `agent_overrides`; validate; merge into the
  run's `effective_config`; return warnings if overrides are present
  (Phase 1 runtime gap).
- Tests touched: `test_model_registry.py`, `test_llm_provider.py`,
  `test_api_config.py`, `test_cost_breakdown.py`. Plus new tests:
  `test_config_models_schema_validation.py`, plus an integration test
  asserting a workflow run's persisted state contains the agents
  snapshot.
