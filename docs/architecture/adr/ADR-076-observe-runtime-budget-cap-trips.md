# ADR-076: Observe Runtime Budget-Cap Trips

## Status

Accepted (2026-06-03). **Implemented** — a fifth deterministic security-event
emit site (`budget_cap_reached`, severity `warning`) fires at the two pre-flight
budget gates (`score_jobs`, `deep_review`) when a run hits the
`MAX_LLM_CALLS_PER_RUN` backstop and drops jobs. Surfaced on the System Dashboard
Reliability section as "runs that hit a cap." Extends ADR-073 (the
`security_events` subsystem and its emit-site contract) and ADR-026 (track
security events). No new table, no new provider, no schema change.

## Context

The system has eight hard execution caps in `app/workflows/limits.py`
(`MAX_LLM_CALLS_PER_RUN = 200`, `MAX_REVIEW_ROUNDS = 2`, `MAX_LLM_CALLS_PER_JOB`,
`MAX_SELECTED_JOBS`, ...). They exist to make runaway cost structurally
impossible: a cost guardrail, in the same family as the SSRF block and the
fidelity reject that ADR-073 already audits.

Two nodes pre-flight the global budget and silently shed work when it is
exhausted:

- `score_jobs` reserves 2 calls/job; the overflow is marked `status="budget_skipped"`.
- `deep_review` reserves `MAX_REVIEW_ROUNDS * 2` calls/job; the overflow is dropped.

Both branches do exactly one thing on a trip: `logger.warning(...)`. The trip
writes **no** `security_event`, `agent_event`, or `run_metrics` row. So a run
that quietly dropped half its jobs to the backstop leaves no queryable trace: it
is invisible to the System Dashboard, to `system_health`, and to any
profile-scoped read. The existing `cost_cap_violation` event does **not** cover
this — it fires only when a human sets an over-budget model in config / kickoff
override validation (`config.py`, `workflows.py`), never when a *run* hits the
wall at execution time.

This is the same shape as the blind spot ADR-075 closed: we instrumented spend
(`llm_calls`) thoroughly, but left the guardrail that *governs* spend
uninstrumented. The cost meter can show a run that came in cheap precisely
*because* the cap silently truncated it, with nothing to say so. That is the
opposite of what an observability layer is for.

The observation cost here is near zero (the trip points already exist; we add one
never-crash append), so the cost/benefit math is one-sided. This is the rare gap
worth closing rather than acknowledging.

## Decision

**Emit a `budget_cap_reached` security event (severity `warning`) at both
pre-flight budget gates, and surface a "runs that hit a cap" signal on the System
Dashboard Reliability section.**

1. **Event.** `event_type = "budget_cap_reached"`, `severity = "warning"` (ADR-073
   scale: a guardrail tripped). Both `score_jobs` and `deep_review` already hold an
   injected `ObservabilityService`, so they call `log_security_event(...)` directly
   (no `emit_security_event_safe` fallback needed; that helper is for run-less call
   sites). The existing append is never-crash, so a missing audit row can never
   break a run.

2. **PII-safe description, defined once.** A shared helper
   `budget_cap_security_description(node, skipped, calls_used, limit)` in
   `observability_service.py` (mirroring `fidelity_review_security_description`)
   returns counts + the node name + numeric call figures **only** — never job
   content, titles, URLs, or identifiers. Both nodes use it so the wording and the
   PII contract are tested in one place. Example:
   `"deep_review budget cap: skipped 4 job(s), 196/200 calls used"`.

3. **Surface.** `system_health.reliability_summary` gains a `runs_hit_cap` field:
   the count of distinct `workflow_run_id`s with a `budget_cap_reached` event in
   the scoped window (reusing `SecurityRepository.list_for_user`, so the
   COALESCE-to-`"0"` profile scoping stays in one place). The System Dashboard
   Reliability section renders it as a metric. It also appears automatically in
   `security_summary.by_type` and the by-profile drilldown, because it is a
   first-class security event.

### Why `security_events`, not a new table

Minimalism is the thesis of this layer (vendor-free, four append-only tables, one
middleware). A budget-cap trip is a guardrail event; `cost_cap_violation` already
lives in `security_events` as a `warning`. Reusing the table keeps the new signal
inside the wired surface, inherits the never-crash write, the PII-safety tests,
the profile scoping, and the dashboard rollups for free. A dedicated table or an
external tracer would buy nothing and contradict the design.

### Why both nodes, not one

`score_jobs` and `deep_review` are independent budget gates that can each trip on
their own (scoring can exhaust the budget before review is reached, or review can
exhaust what scoring left). Auditing only one would leave a half-observed
guardrail — the exact failure mode this ADR exists to remove.

## Consequences

- **Positive.** A truncated run is now visible and attributable. The "cheap run"
  ambiguity (cheap because nothing matched vs. cheap because the cap truncated it)
  is resolved by a single dashboard line. The guardrail is no longer the one
  unobserved control in an otherwise fully-instrumented spend path.
- **Cost.** One extra `security_events` row per node per truncated run (zero on the
  common path where nothing is skipped). Negligible at this app's scale.
- **Forcing function.** `tests/v2/test_security_events.py` gains a behavioral test
  asserting both nodes emit `budget_cap_reached` on a trip and a PII-safety test on
  the description helper. The `>= 4` emit-site invariant continues to hold (now
  five distinct deterministic sites).
- **Docs.** CLAUDE.md security-event rules updated from four to five deterministic
  emit sites; `observability.md` and `security_observability_design.md` note the new
  event; `data_model.md` `security_events` event-type enumeration extended.

## Alternatives considered

- **Acknowledge, do not build.** Rejected: the observation cost is near zero and
  the gap directly undercuts the spend layer's credibility (a cost dashboard that
  cannot tell you a run was truncated by cost).
- **New `budget_events` table.** Rejected: contradicts the minimal-surface design;
  `security_events` already models guardrail trips.
- **Reuse `cost_cap_violation`.** Rejected: that type means "a human configured an
  over-budget model," a distinct cause; conflating the two would make both
  un-queryable. A distinct `event_type` keeps each answerable.
- **Emit from `limits.py` instead of the nodes.** Rejected: the pre-flight math
  lives in the nodes, which also hold the `ObservabilityService` handle and the run
  context; `limits.py` is a pure helper module with neither.
