# ADR-078: Observe the Structured-Output Repair Rate (Tier-1 Drift Proxy)

## Status

Accepted (2026-06-03). **Implemented** — when `ClaudeProvider` runs a schema-repair
pass, the agent now emits a `schema_repaired` `agent_events` row, so the per-agent
structured-output repair rate is queryable and trendable as a behavioral-drift
proxy. Surfaced on the System Dashboard Reliability section. Extends ADR-077
(failed-call attribution) and ADR-023 (observability first-class). No schema change.

## Context

Article 11's thesis is that observability is a cost/benefit decision. The one
observability dimension the system cannot see today is **behavioral / semantic
drift**: the model's outputs changing over time for equivalent inputs (the provider
swaps the backend, re-quantizes, retrains). True semantic-drift detection needs a
baseline registry + an embedding/judge comparison — deliberately out of scope here
and the core of Article 12.

But drift has a cheap **proxy** tier that fits this system's shape, and the purest
proxy is already happening unobserved. `ClaudeProvider.complete()` runs a
**schema-repair pass** when the model returns well-formed JSON that fails the
Pydantic schema (`_attempt_schema_repair`, fired once). A rising structured-output
repair rate is a recognized early warning that the model's output shape drifted or
the provider changed under you ([eastondev](https://eastondev.com/blog/en/posts/ai/20260506-llm-structured-output/),
[VentureBeat](https://venturebeat.com/infrastructure/monitoring-llm-behavior-drift-retries-and-refusal-patterns)).

Today a repair is invisible: a repaired-but-successful call logs `agent_events`
`status="completed"`, indistinguishable from a clean call, and ADR-077 captures the
repaired call's *cost* but not the fact that a repair happened. So the system pays
for repairs and cannot trend them — the cheapest available drift signal is on the
floor.

## Decision

**Emit a `schema_repaired` `agent_events` row whenever a schema-repair pass runs,
and surface the per-agent repair rate on the System Dashboard.**

1. **Carry the signal up from the provider.** `LLMUsage` gains
   `schema_repairs: int = 0`. `ClaudeProvider` records whether a repair fired for
   the current call (thread-local, race-free like usage) and exposes it via a new
   `last_call_schema_repairs()` hook (base `LLMClient` returns `0`). The default
   `complete_with_usage` populates `LLMUsage.schema_repairs` from that hook, and the
   ADR-077 failure path attaches it to `LLMProviderError.usage` — so both the
   success and the repair-exhausted-failure paths know a repair happened.

2. **Log it as a lifecycle-quality event.** `BaseAgent._run` calls
   `ObservabilityService.log_schema_repair(workflow_id, agent_name)` when
   `usage.schema_repairs > 0`, on both the success and failure paths. This writes an
   `agent_events` row with `event_type="schema_repaired"`, `status="repaired"`, and
   **`duration_ms=None`**. The null duration keeps it out of the latency
   percentiles (`performance_summary` filters null durations), and `status="repaired"`
   keeps it out of the failure rollups (`reliability_summary` filters
   `status='failed'`). So the new event pollutes no existing aggregate.

3. **Surface the rate.** `system_health.reliability_summary` gains `schema_repairs`
   (count of `schema_repaired` events in the scoped window). The Reliability section
   renders it as a "Schema repairs (drift proxy)" metric beside "Runs hit cap." The
   per-call cost of the repair is already in `llm_calls` (ADR-077), so repair count
   over call count gives the rate.

### Why reuse `agent_events` (no new table, no new column)

`event_type` is already free TEXT carrying the agent lifecycle
(`started`/`completed`/`failed`); a repair is a lifecycle-quality event about that
same agent call, so it is the natural home. Adding a column to `llm_calls` or a new
`drift_events` table would buy nothing and break the minimal-surface design — the
same reasoning ADR-076 used for `security_events` and ADR-077 used to avoid an
`llm_calls` status column. The null-duration / distinct-status choice means the
overload is invisible to every existing query.

### Scope: a proxy, not drift detection

This is explicitly Tier-1: it detects *that something changed* (output shape no
longer parses first time), cheaply, from a signal we already generate. It does
**not** detect that output *meaning* changed for a fixed input — that is Tier-2
(baseline registry + judge/embedding compare), which stays Article 12's subject.
Naming this boundary keeps Articles 11 and 12 non-overlapping.

## Consequences

- **Positive.** The system's cheapest behavioral-drift signal is now observable and
  trendable per agent; a provider-side output-shape change shows up as a rising
  repair rate before it shows up as outright failures. Pairs with ADR-077 (repair
  cost) to make the repair path fully accounted: cost in `llm_calls`, occurrence in
  `agent_events`.
- **Cost.** One extra never-crash `agent_events` append only when a repair fires
  (zero on the clean path). Negligible.
- **Known residual.** Only `ClaudeProvider` reports repairs; `OpenAIProvider`
  returns the base `0` until it implements the hook. The `BaseAgent` + dashboard
  side is provider-agnostic and picks it up for free once it does. Tracked follow-up
  (same shape as ADR-077's OpenAI debt).
- **Forcing function.** `test_structured_output_drift.py` asserts the provider sets
  `schema_repairs` on a repair, that `BaseAgent` emits `schema_repaired` on both
  paths, and that `reliability_summary` counts it.

## Alternatives considered

- **Validator (Fidelity/Auditor) pass-reject-rate trend.** A valid Tier-1 proxy too,
  and read-only, but it measures output *quality* drift and the data is already
  partly surfaced (`human_decisions`, `unsupported_claim`). The repair rate is a
  purer, currently-invisible *shape*-drift signal and ties to ADR-077, so it ships
  first; the validator trend is a cheap follow-on.
- **Add `schema_repairs` to `llm_calls`.** Rejected: schema churn across the read
  stack for a signal `agent_events` can carry as a row.
- **Build Tier-2 (baseline registry) now.** Rejected: costs eval calls, needs a
  golden set, and belongs to Article 12 by the locked series plan.
