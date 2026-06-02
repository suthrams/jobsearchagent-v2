# ADR-074: Close the Remaining Observability Gaps

## Status

Proposed (2026-06-02). Catalog ADR — ratifies the gaps and the intended fixes;
implementation is phased (priority order below). Each phase may land under this
ADR or spin out its own if it grows a real contract.

Follows ADR-073 (which wired the first dead audit table, `security_events`) and
implements ADR-023 (Make Observability First-Class). Touches ADR-059 / Article 8
(human-decision accountability) and the Article 11 observability material
(thread-local cost race, out-of-graph attribution).

## Context

Wiring `security_events` (ADR-073) exposed a recurring pattern: observability
**infrastructure** (table + repository + an `ObservabilityService` method) was
built, injected, and then never called. An audit of the full `ObservabilityService`
surface — counting call sites in `app/` for every `log_*` / metrics method — found
more of the same, plus a known cost-attribution race and some in-graph-only
coverage. Evidence (call-site counts, 2026-06-02):

| Method / table | Call sites | State |
|---|---|---|
| `log_human_decision` -> `human_decisions` | 0 | **dead** — table never written |
| `log_step_started/completed/failed` -> `step_executions` | 0 | **dead** — table never written |
| `init_run_metrics` / `finalize_run_metrics` -> `run_metrics` | in-graph only | partial — out-of-graph runs excluded |
| `last_call_usage` (thread-local) | `custom_url_scraper.py` + `base_agent` internal | deprecated path still live |

These are real observability gaps, not just unused code: each is a question the
system cannot currently answer.

## Decision

Close the gaps, in priority order. Every fix routes through `ObservabilityService`
(never the repositories directly) and inherits its never-crash contract (a missing
audit row must never break a run or a user action), mirroring ADR-073.

### Gap 1 (HIGH) — `human_decisions` is a dead audit table

Human decisions are persisted only in **domain** tables
(`tailored_resumes.decision`, `resume_clinic_reviews.decision`); the cross-cutting
`human_decisions` audit table has zero writers. There is no unified "who decided
what, when, on which artifact" trail — an accountability gap (ADR-059 / Article 8),
not just an observability one.

**Fix:** call `log_human_decision(...)` from the two decision endpoints
(`submit_tailoring_decision`, `submit_resume_clinic_decision`) alongside the
existing domain-table write, recording `decision_type` (`tailoring` / `clinic`),
`decision_value` (approve/revise/reject/edit), a PII-safe payload (artifact id +
counts, never resume content), and `presented_at`/`decided_at`. Surface it as a
**Decisions** strip on the System Dashboard (governance view), profile-scoped like
the rest (ADR-062). Forcing-function test: `log_human_decision` must have >0 call
sites (same guard ADR-073 added for security events).

### Gap 2 (MEDIUM) — `step_executions` is a dead audit table

`log_step_*` is never called, so workflow node-level timing/transitions are
unrecorded. `agent_events` already gives per-agent timing (the System Dashboard
Performance section uses it), so this adds node-level granularity (how long a step
took *including* non-agent work), not first-light timing.

**Fix:** wrap each LangGraph node via `log_step_started` / `log_step_completed`
(or a thin decorator in the graph builder) so every step transition is recorded.
Feed step durations into the Performance section. Lower priority — agent_events
covers most of the signal.

### Gap 3 (MEDIUM) — out-of-graph runs get no `run_metrics`

`init_run_metrics` / `finalize_run_metrics` run only in-graph
(`register_run` -> `generate_report`). The out-of-graph operations (Resume Clinic,
tailoring, deep-review, interview-prep) write a `workflow_runs` row but no
`run_metrics` row, so run-level duration/rollup is missing for roughly half of all
runs. Cost is unaffected (the dashboard reads `llm_calls` directly).

**Fix:** have the out-of-graph runners init+finalize `run_metrics` around their
agent calls (they already write the correlation `workflow_runs` row, ADR-066/055),
or compute run_metrics lazily from `llm_calls` on read. Prefer the lazy read to
avoid threading metrics through every runner.

### Gap 4 (MEDIUM) — deprecated thread-local `last_call_usage` still live

`custom_url_scraper.py` records its LLM-fallback cost via the thread-local
`last_call_usage()` side-channel — the exact race typed `complete_with_usage()`
was introduced to remove (Article 11). Under concurrent discovery it can
misattribute cost. That call also logs an `llm_call` but **no `agent_event`**
(it bypasses `BaseAgent`).

**Fix:** migrate the scraper's extraction call to `complete_with_usage()` and emit
an `agent_event` for `custom_url_extractor` so it is attributable like every other
agent. Audit for any remaining `last_call_usage` callers and deprecate the method
once none remain.

### Minor (documented, not scheduled)

- **Ad-hoc resume-upload parse cost is unattributed** — `ResumeParser.parse_pdf`
  with `workflow_id=None` (upload path) logs no run-linked `llm_call`. Attribute it
  to a lightweight correlation run (like the clinic) if upload cost needs to be
  visible.
- **`observability.md` documents 7 methods that do not exist**
  (`log_state_transition`, `log_tool_event`, `log_error`, `log_review_round`,
  `update_workflow_status`, `complete_workflow`, `fail_workflow`) — doc/impl drift;
  correct the doc to the real surface.

## Options considered

- **Catalog ADR + phased fixes (chosen).** One record of the gaps and intended
  fixes; build in priority order so each lands verifiable. Matches how ADR-073
  closed the first of the set.
- **One big "wire everything" change.** Rejected — couples four unrelated fixes
  (decisions, steps, metrics, a race) into one diff; harder to review and revert.
- **Leave as unused code.** Rejected — these are not dead code to delete; they are
  audit questions the system should answer. (Contrast the ADR-070-era dead-code
  audit, which removed genuinely unused modules.)

## Consequences

### Positive

- A unified human-decision audit trail (Gap 1) — the accountability record
  ADR-059 implies but never persisted centrally.
- Node-level timing (Gap 2) and out-of-graph run metrics (Gap 3) complete the
  System Dashboard's Performance/Reliability picture.
- Removing the last thread-local cost path (Gap 4) closes the Article 11 race for
  real, not just for the agents already migrated.
- A forcing-function test per newly-wired table keeps it from going dark again.

### Tradeoffs / Neutral

- More emit points to maintain; mitigated by the never-crash contract and
  per-gap forcing-function tests.
- No schema changes — every table already exists. This ADR only starts writing to
  them and adds reads/UI.
- Phased: priority is Gap 1 (next build) > Gaps 2-4 > minors. Sequencing is not
  binding; the user picks scope per phase.

## References

- ADR-073 — Wire security-event emit sites (the first gap in this set, closed).
- ADR-023 — Make Observability First-Class.
- ADR-059 / Article 8 — human-decision accountability (Gap 1's "why").
- ADR-055 / ADR-066 — out-of-graph operations (Gap 3's scope).
- `docs/architecture/observability.md` — the surface (and the doc drift to fix).
- Companion: `docs/architecture/security_observability_design.md` (ADR-073's
  design; the System Dashboard these fixes extend).
