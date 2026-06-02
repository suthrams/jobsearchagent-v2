# ADR-073: Wire Security-Event Emit Sites and a Unified System Dashboard

## Status

Accepted (2026-06-02). Proposed -> implementation pending.

Implements ADR-026 (Track Security Events — the *why*, accepted but never wired)
and ADR-023 (Make Observability First-Class). Builds on ADR-062 (multi-user
profiles — the per-profile read scoping this view honors), ADR-069/070 (the PII
redaction seam that becomes one emit site), ADR-019 (untrusted job descriptions),
and the SSRF defense in `app/services/url_safety.py`.

## Context

The security-event subsystem is **fully built but has zero emit sites**:

- `security_events` table (DDL, `idx_security_created_at`, 180-day retention,
  purge cascade in ADR-070) — exists.
- `SecurityRepository` (append-only `create` + `get_by_run`) — exists.
- `ObservabilityService.log_security_event(workflow_id, event_type, severity,
  description)` — exists, swallows repository exceptions (observability must
  never crash a run), and is unit-tested.
- **Nobody calls `log_security_event`.** ADR-026 names the intended events —
  prompt-injection warnings, blocked tool calls, PII redaction, schema failures,
  unsupported claims, cost limits — none are emitted.

So the system already *detects* most of these conditions deterministically and
then throws the signal away: an SSRF block becomes an `errors[]` string, a PII
redaction is silent, a Fidelity reject lives only inside a JSON blob, a cost-cap
violation raises an HTTP 422 with no audit trail. The audit table designed to
make security posture visible is empty.

Two problems compound this:

1. **No emit sites.** The detections exist in scattered places (a scraper, a
   workflow node, two routers, a runner) but none records a security event.
2. **No visualization.** Even once written, `SecurityRepository` only exposes
   `get_by_run` — a per-workflow read. Security posture is a *system-wide*
   operational concern (an operator wants "across all my runs, how many blocks /
   redactions / rejects, and how recently"), not a per-run detail buried in one
   workflow's page. The same is true of the other operational pillars
   (Performance, Scalability, Reliability — "PSSR", the review axis this project
   audits on every change) and of cost, which already has a system-wide screen.

The Cost Dashboard (`app/ui/views/cost_dashboard.py`) already proves the right
shape: `llm_calls` rows are stored **per run** (with a `workflow_run_id`
correlation id) but **visualized system-wide**, profile-scoped via an "All
profiles" toggle and filtered by a time window. Security and the rest of PSSR
should follow that same store-per-run / view-system-level pattern, and they
should share one screen rather than scattering across the UI.

## Decision

Two coupled decisions:

### Part 1 — Wire all deterministic security-event emit sites

Emit a security event at every place the system **already** detects a
security-relevant condition deterministically. No new detection heuristics in
this ADR (a job-description prompt-injection detector is explicitly deferred —
see Options). Every emit goes through `ObservabilityService.log_security_event`,
inheriting its never-crash guarantee.

Storage stays **per-workflow**: `security_events.workflow_run_id` remains
`NOT NULL`, preserving the correlation id. Events with no run context use the
sentinel run id `"system"` (see Part 1E).

The emit-site catalog (event_type / severity / source):

| event_type | severity | Source (already-existing detection) |
|---|---|---|
| `blocked_url_fetch` | `high` | `url_safety.validate_url_for_fetch` -> `UnsafeURLError`, caught in `CustomUrlScraper._scrape_one` |
| `pii_redacted` | `info` | `redact_pii_for_llm` in the `load_resume` node (ADR-069/070) |
| `unsupported_claim` | `warning` | `FidelityReviewer` result (`approval_recommendation == "reject"` OR any `unsupported_claims` / `fabricated_metrics`) in the tailoring router and `resume_clinic_runner` |
| `cost_cap_violation` | `warning` | high-volume agent assigned an unsafe model in `config.py` (settings edit) and `workflows.py` (kickoff override) |

**1A. Blocked URL fetch (SSRF).** `CustomUrlScraper` already receives
`observability` + `workflow_id` (for the LLM-fallback `llm_calls` row). In the
`except UnsafeURLError` branch of `_scrape_one`, emit
`log_security_event(workflow_id, "blocked_url_fetch", "high", description)`. The
description records the **reason class and host**, never any fetched content:
`"Blocked unsafe URL (loopback address ... not allowed): host=<host>"`. This is
the strongest signal — a real attack-surface defense that is currently invisible.

**1B. PII redaction.** In the `load_resume` node, after `redact_pii_for_llm`,
compare the original profile against the redacted one to count which **direct
identifier fields** were actually dropped/changed (`name`, `email`, `location`,
`file_name`, plus whether `raw_text` was present). If any were, emit
`log_security_event(workflow_id, "pii_redacted", "info", description)` where the
description is **counts and field names only, never values**:
`"Redacted 4 direct identifiers before LLM context: name, email, location,
raw_text"`. Severity `info` — this is a control working as designed, logged for
auditability, not an alarm.

**1C. Unsupported claims / fabrication.** After `FidelityReviewer.run(...)` in
the tailoring router (`trigger_tailoring`) and in `resume_clinic_runner`, when
the review recommends `reject` or reports any `unsupported_claims` /
`fabricated_metrics`, emit `log_security_event(workflow_id, "unsupported_claim",
"warning", description)`. The description is **counts + status only** (claim text
can echo resume content):
`"Fidelity flagged 3 unsupported claim(s), 1 fabricated metric(s);
recommendation=reject"`.

**1D. Cost-cap violation.** Before raising the existing `cost_cap_violation`
HTTP 422 in `config.py` and `workflows.py`, emit
`log_security_event(run_id, "cost_cap_violation", "warning", description)` naming
the agent + model. Neither site has a started run (`_resolve_agent_snapshot`
runs before the kickoff `workflow_id` is created; the config endpoint has no run
at all), so both use the `"system"` sentinel run id.

**1E. The `"system"` sentinel.** Security events with no run context use
`workflow_run_id = "system"`. This satisfies the `NOT NULL` column without
fabricating a fake run, and the system-level read (Part 2) `LEFT JOIN`s
`workflow_runs` and `COALESCE`s a missing `user_id` to `"0"`, so sentinel and
legacy/orphan events surface under the default profile and in the all-profiles
view. `"system"` is reserved — it is never a real workflow id (those are UUIDs).

Every description is constructed to be **PII-safe by construction** (counts,
field names, reason classes, hosts — never resume content, never the candidate's
identifiers, never fetched page text). This is itself a security property
(ADR-069's "summaries not raw content in logs") and is covered by a test.

### Part 2 — Unified System Dashboard (store per run, view system-level)

Rename **Cost Dashboard -> System Dashboard** and grow it from a single-purpose
cost screen into one operational pane with sections for the PSSR axis plus
Security and Cost. All sections share the existing window + "All profiles"
controls and are **profile-scoped by default** (ADR-062).

Sections (each reads existing observability data; only Security depends on
Part 1):

- **Security** — `security_events` aggregated by `event_type` and `severity`,
  plus a recent-events table and a per-run drill-through. (New — depends on
  Part 1.)
- **Performance** — latency from data already captured: `llm_calls.latency_ms`
  and `agent_events.duration_ms` (p50 / p95, slowest agents). (New aggregation,
  no new instrumentation.)
- **Reliability** — failure/retry signal from `agent_events` (`status='failed'`)
  and `workflow_runs` terminal status; surfaces run success rate and recent
  failures. (New aggregation, no new instrumentation.)
- **Scalability** — a small throughput strip: jobs/run, runs/day, peak
  concurrency proxy. Deliberately light — a single-node SQLite app has little
  true scalability signal; documented as the thinnest pillar.
- **Cost** — the existing Cost Dashboard content, refactored verbatim into a
  section function. Unchanged behavior.

Read + aggregation layer:

- `SecurityRepository` gains `list_for_user(user_id, days=None)` (join
  `workflow_runs.user_id`, `COALESCE` orphans/sentinel to `"0"`, optional window)
  and a system-wide variant. `get_by_run` is retained for the per-run
  drill-through.
- A new deterministic service `app/services/system_health.py` holds the
  profile-scoped aggregations for Security, Performance, Reliability, and
  Scalability — mirroring `app/services/cost_breakdown.py` (pure SQL reads, no
  LLM, `user_id`-scoped). The UI view stays a thin renderer over these services,
  consistent with the read-path/control-path split in `ui_architecture.md`.

**Profile drilldown.** Beyond the binary active-profile / all-profiles toggle, the
dashboard supports a profile -> run -> job drilldown: in all-profiles mode a
by-profile breakdown (`system_health.profiles_overview`) is shown; clicking a
profile re-scopes every section to that `user_id` via a session-state read-time
view override (`dashboard_profile_filter`) that never mutates the acting identity
(`current_user_id`) and adds no auth check (ADR-062 cooperative isolation).
Run-less sentinel (`"system"`) and legacy events COALESCE to the `"0"` bucket and
are excluded from a specific non-zero profile's drilldown — they are meaningful
only at the system level. See the design doc Section 5.3 for the full model.

Nav/registry churn for the rename: `nav.NAV_ITEMS`, `views/__init__.REGISTRY`,
the file `views/cost_dashboard.py -> views/system_dashboard.py`, the back-nav
references in `views/workflow_detail.py`, and `tests/v2/test_ui_structure.py`.

## Options considered

- **Wire emit sites + system-level unified dashboard (chosen).** Reuses existing
  deterministic detections and the proven store-per-run/view-system-level Cost
  Dashboard pattern; gives PSSR + Security + Cost one operational pane.
- **Per-workflow-only visualization** (security events only on Workflow Detail).
  Rejected as the primary surface — an audit trail you must open run-by-run does
  not support posture monitoring. Retained only as the secondary drill-through.
- **Loosen storage to system-level** (drop `workflow_run_id`, or make it
  nullable). Rejected — the correlation id is the whole point of attributable
  observability and is reused by the per-run drill-through. Storage stays
  per-run; only the *view* is system-level. The `"system"` sentinel covers the
  genuinely run-less events without weakening the column.
- **Add a brand-new "Security" screen separate from Cost.** Rejected — the user
  asked for one dashboard with many points (Security, Cost, PSSR). Consolidating
  into one screen matches that and avoids a second nearly-empty screen.
- **Include a job-description prompt-injection detector now.** Deferred — that is
  new detection logic with its own false-positive tuning, not an emit-call over
  an existing detection. ADR-019 keeps treating JDs as untrusted; a detector can
  layer on later as an additional emit site without changing this contract.
- **Build only Security + the shell now, defer PSSR panels.** Considered
  (ADR-034, don't overbuild). Rejected for this pass because Performance and
  Reliability read data already captured in `agent_events` / `llm_calls` /
  `workflow_runs` — the aggregation is cheap and completes the requested vision
  in one coherent change.

## Consequences

### Positive

- The `security_events` table stops being dead infrastructure: SSRF blocks, PII
  redactions, fabrication rejects, and cost-cap violations become a queryable,
  retained, profile-scoped audit trail.
- One operational pane (System Dashboard) shows spend, security posture,
  latency, and reliability together — the "single pane of glass" the
  observability model has always implied.
- A forcing-function test (`log_security_event` must have >0 call sites) prevents
  the subsystem from silently going dead again — the same class of guard as the
  cost-observability and UI-undefined-names invariants.

### Tradeoffs

- More emit points to keep correct as those code paths evolve. Mitigated by the
  never-crash guarantee (a broken emit degrades to a missing audit row, never a
  failed run) and behavioral tests per site.
- The `"system"` sentinel run id is a small modeling wrinkle (a `workflow_run_id`
  that is not a real run). Documented here and in `data_model.md`; the read layer
  handles it via `COALESCE`.
- The System Dashboard grows in size and read cost. Mitigated by the shared
  window filter and indexed reads; the per-section services can be tuned
  independently.

### Neutral

- No schema migration — `security_events` already exists; this only starts
  writing to it and adds read methods. The dashboard rename is a UI move, not a
  data change.
- Default profile scoping keeps every existing screen's behavior; the "All
  profiles" toggle exposes system-wide and sentinel events.
- Docs touched: this ADR + `ADR-000-index.md`; the companion design doc
  `docs/architecture/security_observability_design.md`; `observability.md`,
  `security.model.md`, `data_model.md` (security_events now written + the
  sentinel), `ui_architecture.md` (System Dashboard), `CLAUDE.md` (security
  events wired; dashboard rename), `CHANGELOG.md`, and the wiki/feature/user-guide
  trail.

## References

- ADR-026 — Track Security Events (the accepted-but-unwired decision this
  implements).
- ADR-023 — Make Observability First-Class.
- ADR-062 — Multi-user profiles (the per-profile read scoping).
- ADR-069 / ADR-070 — PII redaction seam + at-rest dedup (one emit site; the
  "summaries not raw content" logging property the descriptions honor).
- ADR-019 — Treat scraped job descriptions as untrusted input (the deferred
  injection-detector emit site).
- ADR-034 — Do not overbuild before proving core workflow (the scope tension
  weighed in Options).
- `app/services/url_safety.py` — the SSRF defense behind `blocked_url_fetch`.
- Companion: `docs/architecture/security_observability_design.md` — solution
  architecture, data flow, impacted-areas matrix, and testing strategy.
