# Changelog

All notable changes are documented here, grouped by date.

---

## 2026-06-09

### Added — "Why jobs were filtered out" panel + auto-navigate to a finished run

Surfaces the relevance pre-filter's per-job rejection reasons, and stops leaving the
user stranded on the live monitor after a run finishes.

- **Filtered-out panel (Search detail).** The relevance pre-filter (ADR-079) and the
  optional clearance filter (ADR-094) already recorded a per-job audit trail in
  `discovery_stats.relevance_drops`, but only the aggregate count ("relevance 18") was
  ever shown. A new collapsible panel on the Search-detail screen lists each dropped
  job by title + company with its mismatch class (Too senior / Too junior / Unrelated /
  Needs clearance) and the one-line reason. Pure helper `build_relevance_drop_rows`
  in `app/ui/formatting.py`; the data already flows through the existing
  `/workflows/{id}/detail` read (no new endpoint).
- **Audit-trail enrichment.** `relevance_filter` node now stores `title` + `company`
  on each `relevance_drops` entry (both the LLM drops and the deterministic clearance
  drops) so the panel is self-contained in run state and needs no second read. Runs
  scored before this change omit them; the panel falls back to the `job_id`.
- **Auto-navigate on completion (Live monitor).** When a run completes while the user
  is watching the live monitor, the UI now hands off to that run's Search-detail page
  (jobs surfaced, scores, the filtered-out panel) instead of leaving them on the static
  activity feed. The fragment flags the hand-off and the top-level `render` calls
  `st.switch_page` (illegal inside a fragment). Failed/cancelled runs stay on the
  monitor so errors remain in view.
- No ADR (additive surfacing + a UX hand-off, no contract change). Docs swept:
  `relevance_filter_design.md`, `ui_architecture.md`. 1022 tests pass; UI smoke 12/12.

### Fixed — Start-button double-submit + orphaned "running" runs after a restart

- **Start button greys out on submit.** "Start Workflow" stayed enabled during the
  "Submitting…" spinner, and since each kickoff gets a fresh Idempotency-Key the
  server couldn't dedupe a double-click - two clicks started two runs. `start_run.py`
  now uses a two-phase submit: the click captures the payload, raises a guard, and
  reruns so the button re-renders disabled ("Submitting…"); the next run executes the
  stashed payload while the button is greyed, then navigates / clears the guard on error.
- **Orphaned runs reconciled at startup.** First cut: a startup hook flipped runs
  left `running`/`cancelling` by a dead process to `failed` (see below — this grew
  into ADR-096's full recovery stack).
- Docs swept: `workflow_model.md`, `ui_architecture.md`. UI smoke 12/12.
  No ADR for the button fix (UX); the run-recovery work became ADR-096.

### Added — Durable run recovery across process restarts (ADR-096)

A workflow executes in an in-process thread pool, and only `register_run` +
`generate_report` write `workflow_runs`. So a process death mid-run (restart, crash,
or the `cannot schedule new futures after interpreter shutdown` error when uvicorn is
stopped while a run executes — the root cause of the career-advisor failure
investigated this session) froze the row at `running` and showed it as perpetually
running. ADR-096 makes a restart **pause** a run instead of **kill** it:

- **Layer 1 — graceful drain (shutdown).** The lifespan shutdown calls
  `drain_inflight_runs(timeout)`, waiting up to a bounded window (default 30s, env
  `WORKFLOW_SHUTDOWN_DRAIN_SECONDS`) for in-flight runs to checkpoint before the
  process exits, so a clean stop never guillotines a run mid-node. All runs submit
  through a new `_submit_run` seam so the drain can track them. The pool is not
  explicitly shut down (process exit handles it; an explicit shutdown would break a
  test harness that re-enters the lifespan).
- **Layer 2 — checkpointed auto-resume (startup).** `recover_orphaned_runs(graph)`
  resumes each orphan from its SqliteSaver checkpoint via `graph.invoke(None, config)`
  (the existing `_retry_graph` path) under a per-run attempt cap
  (`MAX_RESUME_ATTEMPTS = 3`, counter in `state_json.resume_attempts`), and fails only
  the runs that exhaust the cap so a poison run can't loop forever.
- **Layer 3 — reconciliation backstop.** `WorkflowRepository.reconcile_orphaned_runs()`
  (now built on a per-run `mark_failed` + `list_orphaned_runs`) remains the
  fail-everything fallback. Terminal + parked runs are never touched.
- New repo methods: `list_orphaned_runs`, `bump_resume_attempt`, `mark_failed`.
  Single-process assumption (one-worker uvicorn / `--reload`); a multi-worker deploy
  would need a shared run registry first. Complements ADR-082/083 (which cover a live
  process). Docs swept: ADR-096 + index, `workflow_model.md`, CLAUDE.md. 1031 tests
  pass (7 new); UI smoke 12/12.

### Docs — colorful UI/flow diagram set for ui_architecture.md

Six rendered figures (deterministic `figure_renderer`: JSON spec -> HTML/CSS ->
Chromium PNG, every label literal + exact), embedded across `ui_architecture.md`:
the jobseeker-journey navigation map (S4), the one-data-path diagram (S1), the
screen-to-API table (S6), and three flow figures in S7 - a run's lifecycle +
ADR-096 recovery, the discovery funnel + filtered-out surfacing, and the
out-of-graph on-demand (curate-after) ops. Specs in
`tools/figure_renderer/specs/ui_*.json`; re-render with
`python tools/render_figures.py <id>`. Also fixed a stale `db_reader` read-path
sentence in S1 (retired by ADR-075).

---

## 2026-06-07

### Added — My favorite jobs + job-focused Resume Clinic (ADR-090)

A resume-first loop: flag a few jobs, then tailor your resume toward one of them in
the Resume Clinic and export it.

- **My favorite jobs:** a new `favorite_jobs` table + `FavoriteRepository` + three
  endpoints (`GET/POST/DELETE /users/{id}/favorites`). A bounded (25/profile),
  per-profile, **status-free** working set - a filter-input (the positive twin of the
  ADR-057 exclude), NOT application tracking: it stores only a job reference + a
  title/company snapshot + a timestamp. Flag/un-flag with a ⭐ from the **Matches**
  selected-row cluster (+ a ★ marker column) and the **Opportunity** header.
- **Job-focused Resume Clinic:** an optional "Focus a job (from My favorite jobs)"
  dropdown. No focus -> today's job-agnostic review; a focus -> the existing
  evidence-bound tailoring flow (Tailoring Agent + Fidelity + ADR-072 chat + export),
  output a resume tailored to that role. The per-job tailoring flow was extracted into
  a shared `components/tailoring_panel.py` reused by Opportunity and the focused
  Clinic - net-new backend is the favorites CRUD only.
- **No-tracking boundary, enforced twice:** a schema forcing-function test (the
  favorite_jobs column set must stay exactly {job ref + snapshot + timestamp} - a
  status column fails the build) + the extended UI no-tracking scan. Favorites
  deliberately survive a run purge (snapshot persists); they are removed with their
  profile. 963 tests pass; UI smoke 12/12; browser-verified the API + clinic focus.

### Added — Matches as the live home base (ADR-089)

Closes the run-lifecycle friction ADR-088 surfaced: the core loop no longer bounces
between screens. Matches becomes the live home base.

- **State-aware run-status strip on Matches** (new `components/run_status.py`),
  rendered full on Matches and as a slim chip in the sidebar. It branches on the run
  state in job-seeker words: **idle** -> `+ New search`; **running** -> step + elapsed
  + calls + cost with **Watch** / **Cancel** (ADR-083); **awaiting picks** (ADR-060)
  -> **Choose jobs to score**; **done** -> **Report** + new matches flagged **NEW**;
  **failed** -> **What happened**.
- **Auto-refresh while running** via `st.fragment(run_every=5s)` - the strip updates
  live and the app reruns on completion so results appear with no manual Refresh
  (closes the deferred ADR-088 UX-review R-7). Polls the local API only, only while
  running.
- **New search lands back on Matches** after Start (was a static "Watch live"
  message), so the strip drives the rest.
- The sidebar **Active Run** 3-button panel is replaced by the slim chip; the
  run-centric screens (Searches / Live monitor / Search detail / Run report) stay as
  optional drill-downs. No backend change; the no-application-tracking guardrail
  extends to the strip (enforced by `test_ui_structure`). 945 tests; smoke 12/12.

### Fixed — destination reachability after the ADR-088 reorg

Follow-up to the reorg below: the click-through destinations need real entry points
now that they are not sidebar items.

- **Run report was orphaned** - nothing navigated to it (reachable only by URL). Added
  a **Report** button to the Active Run sidebar panel (Detail / Live / Report) and a
  "View run report" button on Search detail for any completed run.
- **New search post-submit guidance was stale** - it pointed at "Live Run Monitor" /
  "Workflow Detail" as sidebar screens. It now explains the Active Run panel tracks
  the run and offers a "Watch live" button straight to Live monitor.
- New `test_ui_structure` invariant: every `DESTINATION_VIEWS` entry must be a
  `_navigate(...)` target somewhere in the UI (would have caught the orphaned report).

### Changed — profile switcher moved to the top-right header

Layout fix from live job-seeker feedback: Streamlit native multipage pins the nav to
the top of the sidebar, which had pushed the profile selector and the Active Run hub
far down the sidebar (out of first glance). The **profile switcher now lives in a
top-right app header** (brand on the left) - the standard "who am I" spot - and with
it gone from the sidebar, the **Active Run** panel (Detail / Live / Report) sits
directly under the nav, visible without scrolling. No behaviour change; profile
switching still re-scopes the UI and reruns (ADR-062).

### Changed — UI journey reorg (ADR-088 Tier 1): native multipage + merged Matches

Reorganize the Streamlit UI around the job-seeker journey instead of the system's
tables. Two Tier-1 phases landed:

- **Phase 0 - native multipage nav.** The UI moves to Streamlit native multipage
  (`st.navigation` / `st.Page`). `nav.py` is the single source of truth for the
  journey groups (`NAV_GROUPS`: FIND / MY OPPORTUNITIES / RESUME + an operator group
  under a rule-glyph header, not a noun), the click-through `DESTINATION_VIEWS`
  (Search detail / Job detail / Live monitor / Run report, registered
  `visibility="hidden"`), and `DISPLAY_TITLE` (the user-facing rename: Workflow
  History -> Searches, System Dashboard -> Spend & Health, Profiles -> Profiles &
  Resumes, Start New Run -> New search). Internal view names (the REGISTRY keys, the
  `_navigate` targets) stay stable, so the rename is one map, not call-site churn.
  `_navigate` now switches via `st.switch_page` (the `_pending_nav` radio two-step is
  gone); the app lands on Matches (`default=True`). The sidebar radio + Cross-Run
  Analytics separator are retired. A `test_ui_structure` invariant asserts no
  user-facing title says "Workflow".
- **Phase 2 - merged Matches.** (prior commit) Top Matches + IC/Architect/Management
  track views + Companies collapse into one `views/matches.py`: a Roles tab with an
  active-track `segmented_control` sort (ADR-071) + a Companies tab, select-row ->
  action buttons, and a branched first-run empty state. `analytics.py` +
  `components/tracks.py` deleted.
- **Phase 3 - contextual filters.** The min-score / search / include-excluded
  controls move out of the always-on global sidebar (where they were inert on every
  screen but Matches - friction #7 in ADR-088) into the Matches view itself, the one
  screen that consumes them. Values persist on the `flt_*` session keys (seeded by
  the entrypoint, written by Matches) so they survive navigation and still feed New
  search's threshold default.
- **Phase 4 - in-app Back on every destination.** The four hidden destinations
  (Search detail / Job detail / Live monitor / Run report) now render an explicit
  in-app Back via a shared `nav.back_button(<origin>)` helper - under native
  multipage the browser Back misleads (UX-review R-1). Labels track `DISPLAY_TITLE`
  automatically. A `test_ui_structure` invariant asserts every destination renders
  one. **Tier 1 complete.**
- **Phase 5 (Tier 2) - the Opportunity page.** A new `views/opportunity.py` is the
  single per-job surface: it merges the read-only Job Detail (fit summary, score,
  resume-gap vs career-gap, deep-review rounds, advice, interview prep) with the full
  per-job action region that used to live on Workflow Detail - deep review on demand,
  the complete tailoring flow (generate draft -> drafts picker -> approve/revise/
  reject/edit decisions -> ADR-072 live chat + export), interview prep on demand, and
  cost hints + the "not auto-selected -> runs deep review first (extra cost)" note.
  Every job click (Matches "Open opportunity", a Search-detail row's "Open") routes
  here; the old read-only Job Detail is deleted. The no-application-tracking guardrail
  (ADR-088 E) holds: the page offers preparation (tailor, interview) + filtering
  (exclude = "hide from future searches") only, with no Apply/Save/status or
  pursuing/shortlist/saved set - enforced by a new `test_ui_structure` scan.
- **Phase 6 (Tier 2) - Workflow Detail shrunk to "Search detail".** Now that the
  per-job actions live on the Opportunity page, the run page drops the per-job
  Review / interview-Prep / Tailoring sections (~258 lines) and keeps the RUN-level
  view: status + metrics, the manual-selection picker (ADR-060), the Find & Score
  jobs table (each row's "Open" routes to Opportunity), the discovered-jobs table,
  and collapsed Diagnostics. No capability lost - the per-job work is on one surface
  instead of split across two. The ADR-072 chat-wiring test now points at
  opportunity.py as the second shared-panel consumer.

**ADR-088 is fully implemented** (both tiers, phases 0-6). Net: ~15 flat nav items ->
7 journey entries + 4 hidden destinations; five redundant analytics screens -> one
track-aware Matches; the per-job payoff is one click from anywhere via the
Opportunity page. 943 tests pass; UI smoke 12/12. Deferred (separate, evidence-gated):
auto-refresh while a run is active (R-7) and the framework-migration evaluation.

Docs swept: `ui_architecture.md` (nav model, package map, screen table, add-a-screen
all brought current - also fixed pre-existing `db_reader` references retired by
ADR-075), `CLAUDE.md` UI note, ADR-088 + `ui_journey_reorg_plan.md` phase status.
The headless smoke harness (`.claude/skills/smoke-test-ui`) now runs the entrypoint
shell once (native nav) then renders each view in isolation. 940 tests pass; smoke
12/12. (Phase 6 below completes the effort.)

---

## 2026-06-06

### Added — Business-rules + settings explainability docs

Two operator-facing reference docs, motivated by the run of funnel/threshold/cost
tweaks making the effective rules hard to see in one place:

- `docs/business_rules.md` - plain-language "what the system decides and why," by
  stage (discover/filter/relevance/score/select/deep-review/advice/interview-prep/
  tailor), plus execution limits, config layering, HITL + scope boundaries (no
  application tracking), and privacy rules. 44 rules across 12 sections.
- `docs/settings_reference.md` - catalog of every config setting
  (search/scoring/scrapers/agents/models/retention): purpose + how each changes a
  run (cost, breadth, strictness, results), how overrides layer, and a "which
  setting do I change to..." map.

Both are drift-free by design: they describe rules/effects and **cite** the
enforcing constant/ADR/config key rather than mirroring numeric values (current
values live in `limits.py` / `config.example.yaml` / `config_model.md`). Wired into
`wiki.md` (top-level doc count 8 -> 10).

### Added — Scoring resume projection (ADR-086) + async-batch design (ADR-087)

- `project_resume_for_scoring()` wraps `trim_resume_profile` (PII seam preserved)
  and drops fields the Scoring Agent never reads - name/metadata, education
  gpa/honors, and the redundant flat `skills` list when `skill_groups` is populated
  (it is the de-duped union). Used for the per-job `_cached` resume block, shrinking
  the payload re-sent on every scoring call. Quality-neutral; savings are
  input-only and modest (scoring is Haiku; output dominates). Added to the PII
  invariant allowlist as a sanctioned wrapper.
- ADR-087 (Proposed, deferred) documents an optional asynchronous Message Batches
  API scoring mode (50% off input+output, no quality risk) and why it is deferred
  (async breaks run-and-watch; needs a new run lifecycle + UI; output dominates
  cost today).

### Added — Week-by-week + per-model cost charts on the System Dashboard

The Cost section gains a "Weekly spend" bar chart (shown in every window, including
All time) and a "Per-model cost" bar chart (the `by_model` rollup was already
computed, just not rendered). New `cost_breakdown.weekly_spend_trend(days)` mirrors
the daily trend. The day-by-day chart already covered the 7- and 30-day windows.

### Changed — Interview prep on-demand by default + verbose-agent conciseness (ADR-085)

Cost cuts driven by a per-profile analysis (output tokens = ~56% of spend; the
in-graph coach auto-fired on nearly every run):

- New `scoring.auto_interview_prep` (bool, **default off**, read via
  `get_auto_interview_prep(state)`): the in-graph interview coach auto-fires only
  when it is on or `user_requested_interview_prep` is set. Otherwise interview prep
  is on-demand via `POST .../interview-prep`. Removes an always-on Sonnet call from
  every run; re-enablable per profile.
- Brevity constraint added to the `resume_critic` / `career_advisor` /
  `interview_coach` / `resume_reviewer` prompts (versions bumped). Output schemas
  unchanged - trims prose only, never drops a required field. Rejected `max_tokens`
  caps (truncate structured JSON) and Haiku downgrades (A/B-validated).

### Added — Liveness + readiness endpoints + dashboard health tile (ADR-084)

The ~30-endpoint API had no health probe - only passive, traffic-driven
`api_requests` observability (ADR-074). Added the active layer:

- `GET /health` (liveness, no I/O) and `GET /readyz` (readiness). `/readyz` probes
  the SHARED dependencies via `app/services/readiness.py` - `database` (critical ->
  `down`/503), `agent_provider` (Anthropic live/mock) + `adzuna` (capabilities ->
  `degraded`/200), `openai` (optional). We deliberately do NOT synthetically probe
  the 30 routes (most mutate).
- Both are unauthenticated (no `?user_id=`), excluded from `api_requests` recording,
  and secret-safe (presence/mode only, never key values).
- New live "System health" tile on the System Dashboard (`GET /readyz` via
  `api_client.get_readiness()`).
- `app/api/routers/health.py`, `app/services/readiness.py`,
  `tests/v2/test_readiness.py` (14 tests). 931 passed; UI smoke 15/15.

### Fixed — test suite was polluting the production observability tables (dashboard 20% API error rate)

The System Dashboard showed a ~20% API error rate. RCA: the app-global "safe"
recorders default to the real `data/v2.db` and are called from sites that bypass
per-test db injection - `record_api_request_safe` (the ADR-074 HTTP middleware) and
`emit_security_event_safe` (the cost-cap helper). So 30 `TestClient` files' worth of
deliberate negative-path assertions (404/422/409/429) and every cost-cap emit were
written to the production tables across many suite runs (all error rows `user_id='0'`,
confined to the test window, counts in multiples of the run count).

- Autouse fixture in `tests/v2/conftest.py` no-ops both helpers at the call-site
  bindings; a full run now adds **0** rows (was ~233/run). The two tests that
  intentionally exercise recording install their own overrides.
- Purged the already-polluted dev-DB rows (`data/v2.db` is gitignored, not in git):
  `api_requests` 6073 -> 0 (all test noise); `security_events` 97 -> 7 (deleted 90
  test `cost_cap_violation` rows; the 7 kept are real `pii_redacted`). Dashboard API
  error rate now 0%.

### Changed — architecture-docs accuracy sweep to the current (post-ADR-059) model

Swept every non-ADR doc under `docs/architecture/` (and the v1 shared-lib docs in
`models/`, `scrapers/`) to match the shipped code:

- Retired the in-graph `interrupt()` / `waiting_for_user` HITL language (ADR-059);
  corrected stale limits (`MAX_REVIEW_ROUNDS` 3->2, `MAX_SELECTED_JOBS` 10->3,
  `MAX_LLM_CALLS_PER_RUN` 100->200); de-brittled the agent count and added the
  relevance-filter + Resume Clinic agents; removed references to deleted components
  (`db_reader`, `skill_normalizer`, `status_manager`, `LaddersConfig`/`LaddersScraper`,
  `glassdoor`/`ladders` sources); flagged unwired long-term memory; and removed
  application-status / tracking content that contradicted the No-application-tracking
  rule.
- Rebuilt the `agent_graph` / `api_surface` / `ui_refactor` diagrams with the
  deterministic figure renderer (now via a per-spec `outDir` that writes straight
  into the committed `docs/architecture/images/`).
- Files: agent_model, agent_graph_overview, patterns, performance_scalability, hitl,
  workflow_model, architecture_overview, implementation_plan, observability,
  state_and_memory_model, api_reference, data_model, ui_model, security.model,
  prompt_and_guardrails_model + the shared-lib docs; `api_client` docstring corrected
  to the ADR-075 single-data-path.

### Changed — pin line-ending handling (.gitattributes)

Added `.gitattributes` (`* text=auto`; `.sh` / git hooks forced LF; binaries marked
`binary`) so EOL handling no longer depends on each clone's `core.autocrlf`. Resolves
the spurious "modified with an empty diff" noise; the repo is LF-normalized.

### Added — Deterministic figure renderer for the article series

- New `tools/render_figures.py` + `tools/figure_renderer/` (HTML/CSS + headless
  Chromium, JSON specs under `specs/`) render the blog-series diagrams
  deterministically with exact, literal text — no image model, no fabrication risk.
  The theme reaches ChatGPT-grade richness (icon chips; `table`/`compare`/`lanes`/
  `cards`/`scene` layouts; highlight + takeaway band) while staying ASCII. Adopted
  as the diagram default for the series; Article 12 specs included. Blog-article
  tooling only — the agentic system is unchanged.

### Changed — Documentation reconciled with architecture + ADRs

- **User-facing docs corrected against the code/ADRs.** `features.md` Section 9
  described the retired in-graph `interrupt()` / `waiting_for_user` HITL path;
  rewritten as the real out-of-graph decision model (ADR-059), and its
  application-status row (which violated the No application tracking rule) removed.
  `disclaimer.md` parser/cache facts fixed (`pdfminer.six`; parsed profile cached in
  the `resumes` table, not `data/profile.json`). README fixed: `db_reader` (deleted
  in ADR-075), `MAX_SELECTED_JOBS` 10->3, `MAX_REVIEW_ROUNDS` 3->2, the routers list,
  and the retired HITL pattern. Stale counts corrected/de-brittled (56->83 ADRs,
  448->917 tests, 8->10 agents). README Agents table gained the Relevance Filter
  (ADR-079) and Resume Reviewer (ADR-066), models confirmed from `tests/model_pins.json`.
- **Wiki index verified + corrected.** All links resolve and every architecture doc
  is reachable; the Document Count table was stale (it listed the v1 doc directories
  deleted in `f78e80f`) and is now accurate, with `docs/incidents/` indexed.
- **CLAUDE.md trimmed ~41%** (45.7K -> 26.8K chars): the Key Invariants collapsed to
  rule + seam + ADR pointer and the structure/reference/status sections condensed.
  No rule, constant, seam, or contract removed.

### Added — write-series-article Claude Code skill

- New project-own skill (`.claude/skills/write-series-article/`) that orchestrates
  the LinkedIn article-series process (frame -> ground -> draft -> diagrams -> verify
  -> promo -> publish), enforcing the frame-before-draft and verify-before-publish
  gates and the never-overwrite-a-published-image rule. Registered in
  `.claude/skills/README.md`.

---

## 2026-06-05

### Added — Run-lifecycle controls: idempotent kickoff + cancellation

Two agentic-API hardening features for the run surface (a workflow run is a real
LLM bill, so a duplicate or runaway run is wasted money).

- **Idempotent kickoff (ADR-082).** `POST /workflows` accepts an optional
  `Idempotency-Key` header. Same key + same body replays the original `202`
  response without starting a second run; same key + different body is
  `409 idempotency_key_reused`. New `idempotency_keys` table + `IdempotencyRepository`
  (insert-first atomic claim on the PK). The Streamlit kickoff (`api_client.start_workflow`)
  sends a fresh key per call.
- **In-flight execution guard (ADR-082).** `POST /workflows/{id}/retry` and
  `/scoring` are guarded by a process-local single-flight registry
  (`app/workflows/run_control.py`); a run already executing returns
  `409 workflow_already_running`. Closes the double-invoke / read-then-act races.
- **Cooperative cancellation (ADR-083).** `POST /workflows/{id}/cancel` requests
  cancellation; `_instrument_step` checks the cancel registry at each node boundary
  and raises `WorkflowCancelled`, and the run wrappers finalize the run to
  `cancelled`. New statuses `cancelling`/`cancelled`; `_read_status` now gives an
  explicit terminal status precedence over the `snapshot.next` heuristic (also
  surfaces a written `failed` that the bare heuristic previously masked). Cancel
  control added to the Streamlit Live Run Monitor.
- Docs swept: ADR-082/083 + index, `api_reference.md`, `api_surface_overview.md`,
  `data_model.md`, `workflow_model.md`, CLAUDE.md. Tests: `tests/v2/test_run_lifecycle.py`
  (run_control, `_instrument_step` cancel, idempotency replay/conflict/no-key,
  cancel endpoint 404/409/202, in-flight guard); `idempotency_keys` added to the
  schema-tables invariant.

---

## 2026-06-04

### Changed — Workflow Detail UX pass

- **Discovered-jobs table now renders for every run** — including runs that scored
  0 jobs (it was nested inside the "has scored jobs" branch, so it was hidden
  exactly when surfacing the unscored set matters most) and past runs (their
  persisted state already carries the descriptions; only `posted_at` is blank on
  pre-ADR-080 runs). Extracted into a shared `_render_discovered()` helper.
- **Unified job actions.** Selecting a row in the scored table now drives one
  consistent button cluster — **View details · Drill in · Exclude** — and the
  separate "Drill into a job" dropdown is removed. The table is the picker.
- **Job-description honesty.** The details modal now shows a prominent
  "Open the full posting" link button, and flags snippet-sized descriptions:
  Adzuna's API returns only a short summary (verified: ~98% of stored descriptions
  are <=750 chars), so the modal text can be far shorter than the live posting.
  ATS-direct (Greenhouse/Lever) and pasted custom URLs store the full text, so the
  note is suppressed for those.

### Changed — Workflow Detail: posting age up front + discovered-jobs surfacing

UI/UX follow-up to ADR-080. The scored-jobs table is reshaped and a discovered-but-
unscored view is added.

- **Posted** is now the 2nd column (after Title) of the Workflow Detail jobs table,
  showing a compact age ("12d" / "today"); `posted_at` was added to the
  `list_workflow_jobs` read.
- The table is trimmed to cut horizontal scroll: the three Reviewed/Advised/Prep
  checkmarks collapse into one `R/A/P` cell and the `Found` column is dropped.
- New **"All discovered jobs"** expander (auto-opens when any are unscored): a
  compact, posted_at-led table from `state.normalized_jobs` flagging each job
  scored / not-scored — surfacing jobs the inner-join scored table can't show
  (manual-parked, budget-skipped, over-cap). Filter-dropped jobs are summarized as
  a one-line funnel ("Filtered out before scoring — age 5, relevance 6").
- Pure, unit-tested helpers in `formatting.py`: `format_posting_age_short`,
  `build_discovered_rows`, `discovery_funnel_summary`. 904 tests (+5), UI smoke 15/15.
- **Job-details modal (`st.dialog`).** Select a row in any jobs table on Workflow
  Detail (scored, discovered, or the manual-selection picker) and "View details"
  opens a modal with the title/company/location/posting-age/URL, the score summary
  if scored, and the full job description — for a quick review without leaving the
  page. Descriptions come from `state` (no extra read). In a manual-selection run
  this lets the user review each posting before choosing which to score; ad-hoc
  re-scoring of a *completed* run is not offered (the scoring endpoint is gated to
  `awaiting_scoring_selection`).

### Added — ATS-direct job sources, prototype (ADR-081)

The root-cause fix for the Adzuna dead-link problem ADR-080 patched. Adzuna's
staleness is structural to being an aggregator; an employer's own ATS board only
returns currently-published postings and the apply URL is the employer's own ATS
page. Both APIs verified live (no auth, 200, full JD, real apply URL, no 429).

- New `app/services/ats_scrapers.py`: `GreenhouseScraper`
  (`boards-api.greenhouse.io`) + `LeverScraper` (`api.lever.co`), implementing the
  v1 `BaseScraper`. Field mappings verified against the live APIs. Built per run by
  `WorkflowDependencies.ats_scraper_factory(roles)` from
  `scrapers.{greenhouse,lever}.companies` (board tokens/slugs; empty = off),
  title-gated by the run's roles, bounded per board, additive alongside Adzuna.
  New `JobSource.GREENHOUSE`/`LEVER` (v1 + v2 + `_SOURCE_MAP`).
- Tradeoff: queried per company, so it needs a curated company list (config).
  Off until a profile lists targets. Follow-ups (company-list sourcing, concurrency,
  dedup quality) tracked in ADR-081 + `spike_job_data_sources.md`.

### Fixed — Adzuna jobs mislabeled as source "manual"

`JobDiscoveryService._SOURCE_MAP` now maps `"indeed" -> JobSource.ADZUNA`. The v1
`AdzunaScraper` tags its results `JobSource.INDEED` ("Adzuna aggregates Indeed"),
which was not in the map, so every Adzuna job fell back to `MANUAL` and showed the
wrong source in the UI. New discoveries now label correctly as `adzuna`.

### Removed — unused job sources

Dropped the unused `GLASSDOOR` / `LADDERS` `JobSource` values (no scrapers since
ADR-063) and the vestigial `LaddersConfig`. `INDEED` is kept (Adzuna maps to it).

### Added — Posting-age staleness signal + opt-in max-age filter (ADR-080)

Live runs surfaced Adzuna listings that render but whose employer "apply" link is
dead. A one-URL diagnostic showed automated link-verification is not viable
(Adzuna 429-blocks server fetches with `Retry-After: 3600`; the dead link is the
employer terminal behind a JS/click-gated apply button; ATS pages soft-expire with
200). So instead we use **posting age** as the reliable, free staleness proxy.

- New per-profile `search.max_posting_age_days` (int; 0/None = off). Deterministic
  filter in `JobDiscoveryService.discover_with_stats` (after the experience filter)
  drops postings older than N days; postings with no parseable `posted_at` are kept.
  Runs UPSTREAM of the ADR-079 relevance filter and scoring, so one age cap gates
  both. Funnel `stats` gains `age_filter_dropped`.
- `posted_at` (already on `JobPosting` from Adzuna `created`) is now persisted on
  the `jobs` row (new column + migration) and surfaced as "Posted N days ago" + a
  stale badge on Job Detail. New `app/services/posting_age_filter.py` helper +
  `format_posting_age` UI formatter. Start New Run gains a max-age input.
- 892 tests (+7). Docs: ADR-080 + `spike_job_data_sources.md` (the parallel
  ATS-direct exploration) + index, data_model (`jobs.posted_at`), config_model,
  workflow_model, architecture_overview, wiki, CLAUDE.md, config.example. NOT run
  live yet.

### Added — Reasoning relevance pre-filter before scoring (ADR-079)

Opt-in per profile (`search.relevance_filter`, off by default). A new in-graph
`RelevanceFilterAgent` (Haiku, one batched call/run) reasons over every discovered
posting on the auto-scoring branch and hard-drops clear seniority/relevance
mismatches BEFORE scoring, so the 2 LLM calls/job that scoring costs are never paid
on the noise. Net cost-negative on a noisy (e.g. fresh-grad) profile — it is the
automated cousin of ADR-060 manual selection (wide net -> cheap LLM triage ->
narrow -> score), with no `interrupt()`.

- **Profile-relative + bidirectional.** Judges each posting against the candidate's
  own band: `too_senior` for early-career, `too_junior` for senior, `unrelated` for
  off-domain. The LLM counterpart to ADR-065's deterministic `exceeds_cap` /
  `below_floor`, reasoning over the whole posting (catches Lead/Staff/Principal /
  substance-senior roles the keyword filters miss).
- **Wiring.** New `relevance_filter` node; three-way `scoring_mode_gate`
  (manual > relevance > score); `get_relevance_filter` + `get_max_discovered_jobs`
  widening; `search.relevance_filter` registered agent (Haiku) + model pin; Start
  New Run toggle.
- **Safety.** Never loses a run — any agent failure / empty / unparseable verdicts
  keeps ALL jobs; drops audited in `discovery_stats.relevance_drops`. Profile enters
  the agent only via `trim_resume_profile()` (ADR-069 seam); PII invariant holds.
- 885 tests (+14), UI smoke 15/15, secret audit clean. Docs: ADR-079 +
  `relevance_filter_design.md` (control/data/agent-graph flow) + index, CLAUDE.md,
  workflow_model, agent_model, agent_graph_overview, config.example, wiki. NOT run
  live yet.

---

## 2026-06-02

### Changed — UI read funnel COMPLETE: db_reader retired (ADR-075 Phases 3-9)

The UI no longer opens `data/v2.db` directly — every read goes through FastAPI.
`app/ui/db_reader.py` is **deleted**; the forcing-function guard
(`test_ui_no_direct_db`) has an empty allowlist.

- **Phase 3** Analytics -> `/dashboard/scored-jobs` (new `/dashboard` router).
- **Phase 4-5** Job Detail + Live Monitor -> a new `reads.py` router
  (`/workflows/{id}/scored-jobs|reviews|interview-prep|steps|agent-events|llm-calls|`
  `jobs/{job}/pipeline|detail`, `/workflows/recent`).
- **Phase 6** Workflow Detail -> per-sub-resource cached endpoints (+ cost-breakdown
  and run-metrics); guard extended to ban DB-reading aggregator imports in views.
- **Phase 7** System Dashboard -> one composite `/dashboard/system` payload.
- **Phase 8** sidebar (`load_recent_workflows`) + the chat panel's resume-profile
  read -> API (`GET /users/{id}/resumes/{rid}/profile`).
- **Phase 9** deleted `db_reader.py`; ported its field-name + cost-truth-source
  invariant tests to the read-services; flipped `ui_architecture.md` to one path
  and removed the "reads bypass the API" language across the docs.

All UI reads now record `api_requests` rows (ADR-074 Gap 5 observes the whole UI
surface), the dual-write hazard is gone (one query definition per read), and the
UI can run on a separate host / behind auth.

### Added — UI read funnel Phase 2: user reads (ADR-075)

Routes the profile resume/clinic reads through the API.

- `services/reads/user_reads.list_user_resumes` -> `GET /users/{id}/resumes`
  (typed `ResumeList` envelope, path-scoped, ~10 ms live) +
  `api_client.list_user_resumes` + `data._cached_user_resumes` (resilient).
- Swapped `load_user_resumes` off `db_reader` in Profiles, Start New Run, and
  Resume Clinic (Start New Run / Resume Clinic now use plain list-of-dicts, no
  pandas dependency for the picker).
- Clinic past-runs panel now reuses the existing `GET /users/{id}/resume-clinic`;
  its repo read (`list_by_user`) is aligned to exclude tailoring-chat sessions
  (`job_id IS NULL`, ADR-072) so it matches the panel semantics
  `load_user_clinic_reviews` had — which is now retired from the view.
- Guard allowlist (`test_ui_no_direct_db`) shrinks from 7 to 4 views.
- Tests: `test_reads_users.py` (4: service ordering/scoping + endpoint contract +
  clinic-panel job-anchored exclusion). Suite 848 -> 852; ruff clean; UI smoke 15/15.

### Added — UI read funnel Phase 0 + 1 (ADR-075)

First slice of routing UI reads through the API instead of opening SQLite
directly. The Phase-1 latency gate passed (`GET /workflows` ~26 ms p95 locally,
far under the bar) and the read now appears in `api_requests` — the observability
blind spot it used to bypass.

- **Phase 0 (foundation):** `app/services/reads/` package + `paging.py`
  (`clamp_limit`/`safe_sort`/`page` — the §B.1 list contract); `WorkflowRunRow` /
  `WorkflowRunList` response models; a resilient `api_client`/`data.py` read
  wrapper that degrades to an empty page when the backend is down (so browse
  views never crash); and a forcing-function guard
  (`tests/v2/test_ui_no_direct_db.py`) that bans `db_reader`/`sqlite3` imports
  from migrated views via a shrinking allowlist.
- **Phase 1 (Workflow History):** `workflow_reads.list_workflow_runs` (the
  History SQL moved out of `db_reader`, plus paging/sorting + the legacy
  job_scores fallback folded in) -> `GET /workflows` (typed `response_model`,
  PFS params, profile-scoped) -> `api_client.list_workflow_runs` ->
  `data._cached_workflow_runs` -> the History view swapped off `db_reader`.
- Tests: `test_reads_workflows.py` (6: service paging/sort/scoping/legacy +
  endpoint contract) + the guard (3). Suite 839 -> 848; ruff clean; UI smoke
  15/15. `db_reader` stays until Phase 9 for the un-migrated screens.

### Changed — ADR-074 minors: resume-upload cost attribution + doc surface

Closes the last two items in ADR-074 (now fully closed).

- **Resume-upload parse cost** is no longer unattributed: `POST /users/{id}/resume`
  creates a lightweight `workflow_type="resume_upload"` correlation `workflow_runs`
  row and passes its id to `parse_pdf`, so the parse LLM call is attributed to the
  acting profile (not COALESCEd to user `"0"`) — same correlation-row pattern as
  the Resume Clinic. A cache hit writes no `llm_call`, leaving the row unused.
- **`observability.md` Section 19** rewritten to the real `ObservabilityService`
  surface (agent/step/llm/decision/security methods + the module-level never-crash
  helpers), replacing 7 proposed-but-never-built method names.
- Also: fixed `test_api_users.py` stubs to supply `workflow_repo`.

### Changed — out-of-graph run rollup + scraper cost race (ADR-074 Gaps 3-4)

Closes the last two functional gaps in ADR-074 (only the documented minors
remain).

- **Gap 3** — `system_health.run_metrics_rollup(workflow_id)`: per-run rollup
  (calls/tokens/cost/wall-clock duration) for ANY run. Returns the finalized
  `run_metrics` row if present, else lazily derives totals from `llm_calls` and
  the span from `MIN/MAX(created_at)` (the ADR's preferred lazy read — no
  init/finalize plumbing in the out-of-graph runners). Surfaced as a "Run rollup"
  line on Workflow Detail. Tests: `tests/v2/test_run_metrics_rollup.py` (3).
- **Gap 4** — `CustomUrlScraper` now uses the typed `complete_with_usage()`
  (bundles result + usage on one thread, closing the `last_call_usage`
  thread-local race) and emits a `custom_url_extractor` `agent_event` so the
  extractor shows in the dashboard's Performance + Reliability sections. The
  legacy two-step survives only as a fallback for test doubles (mirrors
  `BaseAgent._run`); `last_call_usage` is retained, not deprecated. Test added to
  `tests/v2/test_custom_url_scraper.py`.

Suite 835 -> 839 passing.

### Added — API-request observability (ADR-074 Gap 5)

The REST API surface had no observability (CORS was the only middleware). A new
`@app.middleware("http")` now records every request into a new `api_requests`
table via `record_api_request_safe` (never-crash, runs in `finally` so it fires
even on a handler exception).

- **PII-safe by construction**: stores the matched route TEMPLATE
  (`/tailorings/{tailoring_id}`) — never the raw path or query string; unmatched
  routes record `"<unmatched>"`. Captures method, status, latency, and the acting
  `user_id` (`?user_id=`, ADR-062).
- **New table** `api_requests` (the one schema change in ADR-074; Gaps 1-2 reused
  existing tables) + `ApiRequestRepository`; purged on the `observability_days`
  window (independent — no run FK).
- **Read**: `system_health.api_summary` (total, p50/p95 latency, error rate,
  by-endpoint), profile-scoped; surfaced as an **API** section on the System
  Dashboard.
- **Tests**: `tests/v2/test_api_requests.py` (4) — forcing-function (middleware
  registered), middleware behavior (route template not raw id — PII-safe), repo
  scoping, aggregation. Suite 831 -> 835 passing.

### Added — step_executions node-level timing (ADR-074 Gap 2)

The `step_executions` table (dead — `log_step_*` never called) is now **wired**.
Every LangGraph node is wrapped by `workflow_graph._instrument_step`, which logs a
started row before the node and completed/failed after (never-crash), recording
node-level timing + transitions (distinct from per-LLM-call `agent_events`).

- `ObservabilityService.log_step_started` relaxed to accept `WorkflowStep | str`
  (nodes are instrumented by their registered name).
- `system_health.performance_summary` gains `slowest_steps` (node-level p95);
  rendered in the System Dashboard Performance section beside slowest agents.
- Tests: `tests/v2/test_step_executions.py` (5) — forcing-function (builder wraps
  nodes), behavioral (started+completed / started+failed+reraise / never-crash),
  read. Suite 826 -> 831 passing.

### Added — human_decisions audit trail (ADR-074 Gap 1)

The `human_decisions` table (dead since the in-graph HITL was retired in ADR-059)
is now **wired**. The out-of-graph decision endpoints (`POST /tailorings/{id}/
decisions`, `POST /resume-clinic/{id}/decisions`) mirror each approve/revise/
reject/edit into `human_decisions` via `log_artifact_decision` ->
`log_human_decision` (never-crash), alongside the existing domain-table write.

- **PII-safe payload**: ids + flags only (`tailoring_id`/`review_id`, `job_id`,
  `edited`) — never resume content.
- **Read**: `DecisionRepository.list_for_user` (profile-scoped, orphans COALESCE
  to `"0"`) + `system_health.decisions_summary`; surfaced as a **Human decisions**
  section on the System Dashboard (governance/accountability, ADR-059/Article 8).
- **Tests**: `tests/v2/test_human_decisions.py` (5) — forcing-function (emit sites
  must exist), behavioral, PII-safety, scoping.
- Part of **ADR-074** (catalog of remaining observability gaps); Gaps 2-4
  (`step_executions`, out-of-graph `run_metrics`, the thread-local cost race)
  remain open.

### Added — Security-event wiring + unified System Dashboard (ADR-073)

The `security_events` table (built since ADR-026 but never written to) is now
**wired**, and the Cost Dashboard is generalized into a **System Dashboard** that
shows the PSSR axis (Performance, Scalability, Security, Reliability) plus Cost in
one profile-scoped pane.

- **Emit sites** (all reuse existing deterministic detection): `blocked_url_fetch`
  (high) when the SSRF guard rejects a custom URL; `pii_redacted` (info) when
  `load_resume` strips direct identifiers; `unsupported_claim` (warning) when the
  Fidelity Reviewer rejects/flags a draft (tailoring router + clinic runner);
  `cost_cap_violation` (warning) in config-edit + kickoff override validation.
- **Never-crash + PII-safe**: emits route through
  `ObservabilityService.log_security_event` / `emit_security_event_safe` (swallow
  errors); descriptions are counts/field-names/reason-classes/hosts only — never
  resume content. Run-less events use the `SYSTEM_RUN_ID = "system"` sentinel.
- **Read layer**: `SecurityRepository.list_for_user` (LEFT JOIN `workflow_runs`,
  COALESCE sentinel/orphan to `"0"`) + new `app/services/system_health.py`
  (security / performance / reliability / scalability / `profiles_overview`),
  mirroring `cost_breakdown.py`.
- **UI**: `app/ui/views/cost_dashboard.py` -> `system_dashboard.py` (nav +
  registry renamed to "System Dashboard"); sections for Security / Performance /
  Reliability / Scalability / Cost sharing one window + profile control; a
  profile -> run -> job drilldown via a `dashboard_profile_filter` read-time view
  override (no auth — ADR-062 cooperative isolation).
- **Tests**: `tests/v2/test_security_events.py` (12) — a forcing-function
  invariant (emit sites must exist), per-site behavioral checks, PII-safety, and
  read scoping. Suite: 809 -> 821 passing. UI smoke 15/15.
- **Docs**: ADR-073 + index; `docs/architecture/security_observability_design.md`
  (solution architecture + a browser mockup at
  `docs/architecture/mockups/system_dashboard_mockup.html`); CLAUDE.md,
  data_model.md, observability.md, security.model.md, ui_architecture.md.

---

## 2026-06-01

### Added — Per-profile active scoring tracks (ADR-071)

A profile now declares which of the three fixed career tracks
(`ic` / `architect` / `management`) it pursues, via
`effective_config.scoring.tracks` (a subset; default all three). Inactive tracks
are **not scored**, **do not gate deep review**, and are **hidden in the UI**. This
fixes the correctness gap where a spurious `leadership_score` could push an IC-only
profile's job into a paid deep-review + interview-prep pass.

- **Schema**: `JobScore.{technical,architecture,leadership}_score` are now
  `int | None` (null when the track is inactive). `overall_score` / `domain_score`
  stay required.
- **Helpers** (`app/workflows/limits.py`): `VALID_TRACKS`, `TRACK_TO_SCORE_KEY`,
  `get_active_tracks(state)`, `active_track_keys(state)`. `best_track_score` /
  `qualifies_for_deep_review` take `active_keys` (default all three). Threaded
  through `await_job_selection`, `routers.py`, `interview_prep`,
  `constraint_analyzer`.
- **Scoring**: `scoring_agent.txt` -> v2 scores only the active tracks and computes
  `overall_score` across them; `score_jobs` passes `active_tracks` into context.
- **Config**: `ConfigService` validates/clamps `scoring.tracks` (not protected).
- **UI**: Settings multiselect; Start New Run inherits the profile's tracks;
  Workflow Detail renders only active track columns; Job Detail shows only scored
  track metrics; Analytics gates the per-track pages and trims the Companies
  aggregation.
- No DB migration (track scores live in `score_json`; the per-run active set is
  recoverable from `workflow_runs.state_json.effective_config`). Default = all
  three keeps the Primary profile and existing runs unchanged.

### Fixed — Missing `httpx` import in two Streamlit views (BUG-001)

`app/ui/views/workflow_detail.py` and `resume_clinic.py` referenced `httpx`
(in `except httpx.ReadTimeout` / `except httpx.HTTPStatusError`) but never
imported it — a NameError on a live click, lost when the view bodies were lifted
out of the monolithic `streamlit_app.py` during the UI refactor. Added the import.

Established the `bugs/` RCA convention (`bugs/README.md`, `_TEMPLATE.md`,
`BUG-001-...md`) and a forcing-function test
(`tests/v2/test_ui_undefined_names.py`) that statically scans `app/ui/` for
undefined names via the stdlib `symtable` analyzer — catches the whole
"lifted body, dropped import" class.

---

## 2026-05-29

### Fixed — SSRF defense on CustomUrlScraper (boundary check on user-supplied URLs)

Security audit pre-Article 10 found a real SSRF gap in `CustomUrlScraper._fetch`.
The scraper fetches URLs the user pastes in the Start New Run "paste URLs"
field. Before this commit `_fetch` did `httpx.get(url, follow_redirects=True)`
with no scheme allowlist, no host validation, and no per-redirect check. A
user (or attacker pasting on someone else's behalf) could submit:

    file:///etc/passwd                local file read
    http://localhost:6379/            probe local Redis or other internal services
    http://[::1]/internal              IPv6 loopback variant
    http://192.168.1.1/admin          LAN-scan / router probe
    http://10.0.0.1/internal          internal corporate network probe
    http://169.254.169.254/...        cloud instance metadata (AWS/GCP/Azure)

The 25-URL cap and 30s timeout bounded the abuse rate but not the access
surface. This is the classic SSRF pattern.

**Fix.** New `app/services/url_safety.py` with `validate_url_for_fetch`. It
enforces:

- **Scheme allowlist**: only `http` and `https`. `file://`, `ftp://`,
  `gopher://`, `javascript:`, `data:` all rejected.
- **Host presence**: empty / missing hostname rejected.
- **Resolved-IP allowlist (negative form)**: the host is DNS-resolved and
  EVERY returned address must be a routable public address. Any
  loopback / link-local / private (RFC 1918 / ULA) / unspecified /
  multicast / reserved address triggers rejection. Multi-record DNS
  responses are all checked (a hostname that resolves to BOTH a public
  and a private IP is rejected, otherwise an attacker can bypass by
  stacking records).
- **Literal IPs are validated directly** (no DNS round-trip).

`CustomUrlScraper._fetch` now:
- Calls `validate_url_for_fetch(url)` before the first request.
- Sets `follow_redirects=False` and loops manually (max 5 hops),
  re-validating each `Location` target. Without this an attacker
  could submit a public URL that 302s to `http://169.254.169.254/`.
- `_scrape_one` catches `UnsafeURLError` distinctly from `httpx.HTTPError`
  so the workflow `errors[]` log records `"unsafe_url: <reason>"` instead
  of a generic transport failure. Audit-trail clarity matters here.

**Known limitation: DNS rebinding.** The validator resolves the host at
validation time; httpx re-resolves at connect time. An attacker who
controls the authoritative DNS can return a public IP at validation and
a private IP at fetch. Closing that requires pinned-IP fetch (set the
`Host:` header and connect by resolved IP). Out of scope for v1. Real
users of the URL field paste hosts they typed by hand for public job
boards. Documented in the module header.

**Tests** (`tests/v2/test_url_safety.py` + 1 in
`test_custom_url_scraper.py`): 27 new cases. Scheme rejection (file://,
ftp://, gopher://, javascript:, data:), literal-IP rejections (loopback
v4 + v6, AWS metadata, all three RFC 1918 ranges, unspecified, multicast),
hostname rejections (localhost, private-resolved, mixed multi-record),
DNS failure handled, public address passes. Plus an integration test
that `UnsafeURLError` from `_fetch` lands as `"unsafe_url:"` in the
workflow errors[] log. Full suite at 768 (was 741; +27).

**Out of scope (separate work):** the no-auth posture on the API
(`?user_id=` cooperative-only multi-user, documented in
`security.model.md` 4.1), no per-request size caps on JD body fields, no
rate limiting. Those are honest gaps to name in Article 10 rather than
patch in this commit. SSRF was the only finding the audit flagged as
"needs to ship before the article" - it is a concrete code-level bug, not
a documented design choice.

### Fixed — Adzuna senior-exclude was too aggressive; split into two lists + bump results_per_page

Third diagnostic in today's discovery debugging series. The previous two
fixes (dedup narrowing, URL canonicalization) restored re-discovery and
plugged the rotating-token bug, but the cyber-grad profile was still only
seeing 5 raw postings from Adzuna across 36 (role x location) queries.
That ratio (≈0.14 postings per query) was the next thing to chase.

**Root cause.** `SENIOR_TERMS = ["senior", "principal", "staff", "lead",
"director", "vp", "vice president", "head of", "manager", "architect"]`
was being passed verbatim as Adzuna's `what_exclude` parameter. Adzuna
matches `what_exclude` against title AND description, not title alone.
Words like "manager", "lead", "staff", "head" appear in countless entry-
level posting descriptions ("reports to the security manager", "works
with the team lead", "staff member of the SOC", "headquartered in NYC")
and excluded those postings at the source.

A title-level gate is the right place to drop "Senior Security Analyst."
The Adzuna API gate is the wrong place — it gates on text where those
words have legitimate non-senior meanings.

**Fix.** Split SENIOR_TERMS into two lists by purpose:

- `SENIOR_TERMS` (unchanged) — the broader list, used at the LOCAL title
  gate (substring-matched against the JOB TITLE only). Still drops
  "Senior X", "X Lead", "X Manager", "X Principal" etc by title.
- `SENIOR_TERMS_API_EXCLUDE = ["senior", "principal", "vp", "director"]`
  (new) — high-precision tokens that reliably indicate senior content
  wherever they appear in text. Used for Adzuna's `what_exclude`.

`app/api/dependencies.py::_adzuna_factory` wires the narrow list to
`what_exclude` and the broad list to the local title gate. Both behaviors
remain conditional on `search.exclude_senior=true`.

**Also: bumped `results_per_page` 10 -> 25** in `config/config.yaml` and
the example. The narrowed what_exclude lets more matches through per
query; 25 is the new default to take advantage. Free tier max is 50.

**Expected impact.** Cyber-grad profile run that just yielded 5 raw
postings should now produce 30-50+ raw, with senior titles still dropped
by the title gate. Will measure on the next live run.

**Tests** (`tests/v2/test_adr064_discovery.py`): 3 new cases. The load-
bearing one is `test_senior_terms_api_exclude_is_narrower_than_local_gate`
- asserts the invariant directly: the API list must be strictly narrower
than the title gate, and polysemic terms (manager / lead / staff / head /
architect / vice president) must NOT appear in the API list but MUST
appear in the title gate. Catches a future refactor that re-conflates
the two lists. Plus a regression test that the title gate still drops
"Senior X" / "X Lead" / "X Manager" / "Principal X" after the split.
Full suite at 741 (was 738; +3).

### Fixed — Adzuna URL canonicalization (closes the "same job over and over" loop)

Follow-up to the discovery dedup commit earlier today. After shipping the
funnel instrumentation + excluded-only dedup + per-user dedup, the next
live run on the cyber-grad profile (`703cb6fc`) returned a single new
posting again. The funnel showed `dedup_user_scored_dropped: 4`,
`returned: 1` - per-user dedup was working - but the "1 returned" was
still "Digital Network Exploitation Analyst" at the same Adzuna ad ID
the user had already scored five times in prior runs.

Investigation found six rows in the `jobs` table for the same Adzuna ad
(ID `5690461826`), each with a different `jobs.id` UUID, each at a
slightly different URL. The URLs differed only in a rotating session
token: `?se=eFiZbnFZ8RGcOYs2Ni-pJA`, `?se=zFirR-tZ8RGs3Me5X3pAAw`, etc.
Adzuna appends a fresh tracking token to every redirect URL it returns
from search; the ad ID is stable, the query rotates per fetch. Per-URL
dedup could never catch this because the URL itself was different each
time.

**Fix.** New `app/services/url_canonicalizer.py` with `canonicalize_url`.
For Adzuna URLs (host contains "adzuna"), strips the query and fragment,
leaving only `scheme://host/path`. Non-Adzuna URLs pass through
unchanged - we only canonicalize when we know the source rotates
tracking parameters. Called from `JobDiscoveryService.normalize` so every
`JobPosting` carries the canonical URL before any persist or dedup.

The path-only Adzuna URL (`https://www.adzuna.com/land/ad/5690461826`)
is now stable across fetches. Next run on the cyber profile should:

1. Get the canonical URL on first fetch of any new Adzuna ad
2. Skip that URL on the next run via per-user dedup (correctly)
3. Re-surface Adzuna ads that other profiles have scored but this one
   hasn't (since global dedup only drops excluded URLs)

**Historical noise** (intentional non-fix). The `jobs` table still has 6
rows for ad `5690461826` from prior runs (each at a different URL). A
backfill that consolidates them onto the canonical URL would touch
`job_scores`, `tailored_resumes`, `interview_prep`, and other tables
that reference `jobs.id`. Out of scope for this fix. The historical rows
sit as inert noise; future fetches of the same ad will produce ONE new
row at the canonical URL and dedup from there.

**Out of scope (separate diagnostic needed).** The deeper question raised
by run `703cb6fc`: why only 5 raw postings from Adzuna across 6 cyber
roles in 6 cities? That's not the rotating-token bug; it's the Adzuna
search itself. Candidates for next diagnostic: `exclude_senior=true` may
be too aggressive in `what_exclude`, the per-run scraper may be searching
fewer (role x location) pairs than expected, or the title-relevance gate
in `models.filters` may be filtering more than the funnel reveals. Will
chase next if the next live run still feels thin.

**Tests** (`tests/v2/test_url_canonicalizer.py`, +2 in
`test_job_discovery_service.py`): 9 new cases. The load-bearing one is
`test_same_adzuna_ad_two_rotating_tokens_yields_same_canonical` -
asserts the regression scenario directly. Full suite at 738 (was 729;
+9).

### Fixed — Discovery dedup no longer collapses re-discovery + per-user dedup + funnel instrumentation

Live run on the cyber-grad profile surfaced a discovery bug: three
back-to-back runs all returned the same single posting ("Digital Network
Exploitation Analyst @ Booz Allen Hamilton") despite `max_discovered=50`,
`max_scored=10`, `min_match_score=40`, no errors, no timeouts. Diagnosis
traced to `JobDiscoveryService.deduplicate()` line that dropped any URL
already in the `jobs` table — across every user, every prior run. Adzuna
kept returning roughly the same posting set per run; everything was
already persisted; dedup silently dropped all of it. ADR-057 designed this
as a feature ("exclude once, stay excluded"), but it also collapsed
multi-profile + repeat-run discovery into a near-empty result set with no
visible signal anywhere.

This commit ships three fixes that compose:

**1. Discovery funnel instrumentation** (`app/services/job_discovery_service.py`,
`app/workflows/nodes/discover_jobs.py`, `app/workflows/graph_state.py`):
new `discover_with_stats(...)` returns `(postings, stats)` where stats
carries per-stage funnel counts: `per_scraper`, `title_filter_dropped`,
`experience_filter_dropped`, `dedup_batch_dropped`, `dedup_excluded_dropped`,
`dedup_user_scored_dropped`, `max_jobs_truncated`, `returned`. The node
persists this to `state["discovery_stats"]` (declared on
`WorkflowGraphState` so LangGraph doesn't drop it). A summary log line
prints the funnel on every run. Backward-compat: `discover(...)` is now a
thin wrapper that discards stats.

**2. Excluded-only global dedup**: new `JobRepository.url_excluded(url)`
checks `jobs.url = ? AND excluded = 1`. The dedup loop now uses this
instead of the old catch-all `url_exists`. ADR-057's "stay excluded"
semantics are preserved — URLs flagged excluded by ANY user still drop on
every subsequent discovery. URLs that are merely persisted are now
re-discoverable, which is what enables multi-profile and repeat-run
discovery.

**3. Per-user already-scored dedup**: new
`JobRepository.url_scored_by_user(url, user_id)` joins `job_scores ->
workflow_runs (for user_id) -> jobs (for URL match)`. When the node passes
`user_id` to `discover_with_stats`, the dedup loop drops URLs THIS user
has already paid to score in a prior run — the cost saver. Different
users (profiles) score independently, which is the correct semantic for
ADR-062's multi-user model: a job scored by Primary may legitimately be
re-scored by a second profile under different criteria.

Old `url_exists` is retained on the repository for backward compat but is
no longer called by the dedup path.

**Tests** (`tests/v2/test_job_discovery_service.py`): 4 new cases.
`test_deduplicate_does_not_drop_merely_persisted_urls` is the load-bearing
regression test for this specific bug — fails on the old code, passes on
the new. `test_deduplicate_drops_excluded_urls` keeps the ADR-057
invariant under test. `test_deduplicate_drops_urls_already_scored_by_this_user`
and `test_deduplicate_per_user_does_not_drop_other_users_scores` cover the
multi-profile semantics. Full suite at 729 (was 725; +4).

**What this means for the cyber profile.** Next run on the cyber-grad profile
should re-surface the 22 cyber-relevant URLs already in `jobs` (now that
they're not blocked by global dedup), score whatever that profile hasn't already
scored this session (per-user dedup catches the cost concern), and write
the funnel counts to state so the UI / DB can show what got dropped where.

### Changed — Wire `_cached` resume_profile on four high-volume agents (cache hit fix)

Diagnostic on `llm_calls` over the last 30 days showed five agents with
near-0% prompt-cache hit ratio despite consuming the same `resume_profile`
across every call in a workflow run:

| Agent | Calls (30d) | Hit ratio | Why |
|---|---|---|---|
| scoring_agent | 111 | 0.00% | passed resume_profile as per-call field |
| review_auditor | 46 | 0.00% | same |
| resume_critic | 46 | 0.00% | same |
| fidelity_reviewer | 12 | 0.00% | same |
| resume_chat | 8 | 62.34% | already wired via _cached - the reference shape |

`resume_chat` proves the wiring works: pull resume_profile into the
`_cached` dict, and PromptLoader routes it into a second cached system
block with `cache_control: ephemeral`. Anthropic charges 10% of input
rate on the second+ call within the 5-min window.

This commit applies the same shape to the four cold agents:

- `app/workflows/nodes/score_jobs.py`: scoring_agent context now uses
  `_cached: {resume_profile: trim_resume_profile(resume_profile)}`.
  scoring runs 5-wide through `ThreadPoolExecutor`; the cache race is
  partially mitigated by giving all five workers byte-identical cached
  content (the first to land creates the entry; the rest read it).
- `app/services/deep_review_runner.py`: resume_critic + review_auditor
  share one `_cached_profile` dict computed once at the top of
  `review_one_job`. Both agents send byte-identical cached content -
  needed because Anthropic's cache key hashes the exact prefix; any
  drift, even whitespace, misses.
- `app/api/routers/tailoring.py`: the fidelity_reviewer call following
  a tailoring draft now uses `_cached`. The tailoring_agent call right
  above already warms the same key (it has used `_cached` since ADR-053
  cost work), so the fidelity call should read it back within the
  5-min window without a fresh write.
- `app/services/resume_clinic_runner.py::build_fidelity_context_for_overhaul`:
  the chat-revise loop's repeated fidelity calls now share a cached
  resume_profile across turns. raw_text is preserved at top-level
  (per-call) - fidelity reads it for evidence-binding and it must
  match the resume_chat agent's prefix for cross-prompt sharing.

Also: `trim_resume_profile` is now used in all four wirings to drop
`raw_text` from the cached payload (saves 1-2k tokens per cached
block; raw_text isn't read by these agents).

**Invariant test** (`tests/v2/test_workflow_nodes.py`):
`test_score_jobs_passes_resume_profile_via_cached_block` asserts the
scoring node's context carries `_cached.resume_profile` and that
`resume_profile` does NOT appear at top level. Catches a future
refactor that silently un-wires the cache. Full suite at 725 (was 724;
+1).

**Expected impact**: roughly 50-80% cache hit ratio on the four agents
after the first run within a 5-min window. Cost savings depend on
workflow mix; a 10-job run through deep review (~200+ calls touching
resume_profile) should see noticeably reduced input-token spend on
calls 2..N. Will measure on the next live workflow run.

**Out of scope** (separate diagnostic): research_agent (no
resume_profile in its context) and a curious pattern where Block 1
(the always-cached system prefix) shows `cache_creation=0` on ~80% of
calls. That's likely either the concurrent-scoring race, or the
prefix dipping under the 1024-token minimum on some calls, or a
LangChain transformation stripping `cache_control` on a path. Picking
that up next if these wirings don't move the meter enough.

### Added — Headline as a Resume Clinic feedback target (ADR-068 follow-up)

The clinic now treats the resume's `headline` (the one-line positioning
statement under the candidate's name) as a first-class rewrite target on
the same path as `summary`. Before this commit the headline was parsed
into `ResumeProfile.headline` and rendered into every export format, but
neither the reviewer nor the chat agent could touch it - feedback flowed
to summary / experience / skills only and the headline silently passed
through whatever the parser captured.

Closing this is small but the leverage is real: the headline is the
first three words a reviewer reads, and for mid- to senior-career
candidates a sharp value-prop there changes the read of the whole page.

- **Schemas** (`app/schemas/resume_chat.py`): `ChatSectionFocus` and
  `ChangedSection` Literals add `"headline"`. The `ResumeClinicReview`
  shape was already permissive enough (`section_label` is a free `str`)
  so the reviewer can emit a headline rewrite without a schema change.
- **Router** (`app/api/routers/resume_clinic.py`): `ChatBody.section`
  Literal mirrors the chat schema and accepts `"headline"`.
- **Renderer** (`app/services/resume_text_renderer.py`):
  `_apply_rewrites` gets a `section_label == "headline"` branch that
  replaces (or adds) the headline. New helper
  `_replace_or_append_headline` mirrors the summary substitution shape -
  exact-then-substring with append fallback - keeping behaviour
  consistent across the two single-line text fields.
- **Reviewer prompt** (`prompts/agents/resume_reviewer.txt` v2):
  documents headline-as-rewrite-target with explicit criteria
  (sharpness, role/level signal, scannability) and instructs the
  reviewer to propose ADDING a headline (with `original_text=""`) when
  the candidate has none and would benefit from one. Existing
  evidence-binding rules apply unchanged.
- **Chat prompt** (`prompts/agents/resume_chat.txt` v2): `section`
  accepts `"headline"`, the focus rule covers it, and the load-bearing
  isolation invariant ("revise only rewrites whose section_label
  belongs to that section") extends to headline.
- **UI** (`app/ui/streamlit_app.py`): the Refine-with-feedback section
  selectbox adds a `"Headline"` option between "Whole resume" and
  "Summary".
- **Tests**: 3 new cases - renderer rewrites headline exact-match,
  renderer ADDS headline when `original_text=""` and the candidate had
  none, schema accepts `changed_sections=["headline"]`. Full suite at
  724 (was 721; +3).

**Explicit non-goal**: no new `QualityDimension` was added. The
seven-dimension scorecard already covers what would have gone into a
`headline_strength` axis (`clarity` + `seniority_framing` +
`impact_quantification`). Expanding the enum would force a migration
of every persisted clinic row and inflate token cost on every clinic;
the existing rewrite path is enough.

### Added — Cost tracking + per-session cap on Resume Clinic chat (ADR-068 follow-up)

After shipping the chat-revise loop, audit found three gaps in cost
tracking specific to chat: no per-session cap, no real-time cost signal
back to the user, no invariant test that `llm_calls` rows are actually
written. All three are closed in this commit. Same "track cost closely"
principle that drove ADR-058 — but on the chat side, where one clinic
can now run dozens of LLM-backed turns if a user leans in.

- **New limit** (`app/workflows/limits.py`):
  `MAX_CHAT_TURNS_PER_CLINIC = 25`. One chat turn = 1 chat-agent call +
  (when rewrites are emitted) 1 fidelity call. At ~$0.017 uncached per
  turn, 25 turns caps a single clinic at ~$0.45 in chat spend on top of
  the initial reviewer + fidelity (~$0.10). Runtime override via
  `RESUME_CHAT_MAX_TURNS` env var (use sparingly — bias is "block
  first"). The cap counts `resume_chat` rows in `llm_calls` tagged with
  the clinic's `workflow_run_id`, so it survives across sessions and API
  restarts.
- **Cap enforcement** (`app/api/routers/resume_clinic.py`): the chat
  endpoint checks the count BEFORE any LLM call and returns
  `429 chat_turn_cap_reached` (detail mentions the cap + how to
  override). Failing closed here is cheap; failing open is not.
- **Real-time cost return**: `ResumeChatResponse` now carries
  `turns_used`, `max_turns`, `session_cost_usd` (the sum of
  `estimated_cost` across every `llm_calls` row tagged with the
  clinic's `workflow_run_id` — reviewer + every chat turn + every
  fidelity call). The frontend turns this into a sticky meter so the
  user sees their remaining budget before sending the next message.
- **UI meter** (`app/ui/streamlit_app.py`): progress bar (turns_used /
  max_turns) + dollar metric above the chat input. Yellow warning at
  75% of cap, red error at 95%. 429 responses surface the backend's
  detail string instead of a raw httpx error. The meter persists
  across reruns via session_state and is not cleared on "Discard
  chat edits" (the spend is permanent regardless of whether the edits
  are kept).
- **New observability pass-through**:
  `ObservabilityService.get_llm_calls_by_run(workflow_id) -> list[dict]`.
  The router needs per-row filtering the aggregate methods don't
  provide.
- **Invariant tests** (`tests/v2/test_resume_clinic_router.py`):
  5 new cases (1) return shape carries `turns_used` / `max_turns` /
  `session_cost_usd` summed from real seeded rows + the in-test turn,
  (2) 429 at the cap, (3) env-var override honored, (4) response
  reflects overridden cap, (5) cost rollup spans reviewer + chat +
  fidelity rows, not just chat. Wires the existing observability
  MagicMock to an in-memory `llm_call_log` so the tests exercise the
  same `log_llm_call` → `get_llm_calls_by_run` path as production.
  Full suite at 721 (was 716; +5).

**Why this matters**: chat is the first feature where the user — not
the workflow — controls the LLM-call count. Without the cap, a user
who keeps typing pays for every turn; without the meter, they have no
warning before they hit it; without the invariant test, a refactor
that mocks the observability layer could ship a feature that
silently stops writing cost rows. All three close together.

### Added — Resume Clinic chat-revise loop (ADR-068)

The clinic stops being one-shot. Users iterate on the agent's overhaul
through chat: type natural-language feedback, see the resume update in
the preview, repeat. New agent, new endpoint, new UI panel under the
existing clinic results. The `edit` decision is now a real product
surface, not just a backend capability.

- **New agent** `ResumeChatAgent` (`AGENT_NAME = "resume_chat"`,
  `claude-sonnet-4-6` default; pinned in `tests/model_pins.json`). Same
  decision model as the reviewer — separate agent, separate prompt,
  separate cost row, separate model pin. Shares the overhaul output
  shape so the renderer + Fidelity translation glue operate unchanged.
- **New endpoints** in `app/api/routers/resume_clinic.py`:
  - `POST /resume-clinic/{id}/chat` — one revise turn. Body:
    `{message, section?, history?}` → returns
    `{reply, overhaul, fidelity_review, changed_sections}`. Always runs
    Fidelity on rewrites. Persists into `edited_json`; `decision`
    unchanged.
  - `POST /resume-clinic/{id}/discard-edits` — clears `edited_json`,
    `decision`, `decided_at` so the renderer reverts to the agent's
    original overhaul.
- **Composer change** (`app/services/resume_text_renderer.py`):
  `compose_resume` now prefers `edited` whenever populated regardless
  of decision, EXCEPT when `decision == "reject"` (the explicit "throw
  out the overhaul" signal). Lets the preview reflect the chat-edited
  state as it accumulates, before any explicit Save.
- **Runner helper extracted**: `_build_fidelity_context` ->
  `build_fidelity_context_for_overhaul` (public). The chat endpoint
  reuses it for per-turn fidelity checks.
- **Repository methods** (`app/repositories/resume_clinic_repository.py`):
  - `set_edited(clinic_id, edited, fidelity_review=None)` — persists
    a chat turn's output. Decision unchanged.
  - `discard_edits(clinic_id)` — clears `edited_json`, `decision`,
    `decided_at`.
- **API client** (`app/ui/api_client.py`): `chat_resume_clinic(...)` +
  `discard_resume_clinic_edits(...)`.
- **UI** (`app/ui/streamlit_app.py`): new "Refine with feedback" panel
  under the Decision controls. Live preview (renders the composer's
  current state inline), section selectbox, text area, **Send
  feedback** + **Save final edit** + **Discard chat edits** buttons,
  conversation log. Chat history is in-session only (Streamlit
  session_state); not persisted to the DB in v1.
- **Tests**: 13 agent contract + 3 repo + 11 endpoint = 27 new cases.
  Existing renderer tests still pass plus 4 new composer cases
  covering the "edited wins" rule for null / revise / approve / reject
  decisions. Full suite 716 (was 684; +32).
- **Docs**:
  - ADR-068 (the decision, with full design + tradeoffs).
  - `resume_clinic_chat_implementation_walkthrough.md` (the per-file
    plan + summary table of 6 deviations from ADR-068).
  - `resume_clinic_chat_visualization.md` (Mermaid diagrams: the two
    clinic agents at a glance, one chat turn end-to-end, the
    `edited × decision` state machine, where each piece lives, the
    cost shape per session).

**Usage**: open the Resume Clinic, run it as before, then under the
decision controls scroll to "Refine with feedback". Pick a section
focus, type what you want changed ("make the summary shorter"), click
Send. The preview updates. Iterate. Click **Save final edit** to lock
the chat-edited state in as your final draft (sets `decision = "edit"`,
exports use it). Click **Discard chat edits** to revert.

### Added — Delete a resume from a profile (UI + API)

Closes the gap that forced manual `DELETE FROM resumes` SQL during the
ADR-067 validation: there was no in-app way to remove a resume and force a
fresh parse. Same shape as the existing upload flow but in reverse.

- **Repo** (`app/repositories/resume_repository.py`):
  `ResumeRepository.delete(resume_id, user_id) -> int` — hard delete scoped
  to the owning profile (ADR-062 cooperative scoping). Returns the count of
  rows deleted so the caller can detect "not found" without raising.
- **Cascade** (`app/repositories/resume_clinic_repository.py`):
  `ResumeClinicRepository.delete_by_resume(resume_id, user_id) -> int` —
  drops the resume's clinic reviews so the past-runs panel doesn't surface
  broken rows.
- **Endpoint** (`app/api/routers/users.py`):
  `DELETE /users/{user_id}/resume/{resume_id}` -> returns
  `{resume_deleted, clinic_reviews_deleted, user_id, resume_id}` so the UI
  can show the cascade impact. 404 on unknown user, 404 on unknown resume
  OR cross-user attempt (cooperative scoping — same status for both, no
  enumeration leak).
- **Client** (`app/ui/api_client.py`): `delete_resume(user_id, resume_id)`.
- **UI** (`app/ui/streamlit_app.py`): new "Delete a resume from a profile"
  expander in **Profiles → Manage an existing profile**. Profile selector
  -> resume picker -> shows cascade count (clinic reviews) -> confirm
  checkbox -> Delete button. The checkbox is the explicit opt-in for the
  cascade.
- **Preserved**: the resume's job-search `workflow_runs` rows and their
  per-call `llm_calls` rows are NOT deleted — they're audit / cost data and
  unrelated to the fidelity bug that motivates the typical delete-and-
  reupload flow.
- **Tests**: 3 repo + 2 clinic-repo + 4 endpoint = 9 new cases. Full suite
  at 684 (was 675; +9).

**Usage**: open Profiles → Manage an existing profile → Delete a resume,
pick the resume, tick the cascade-confirm checkbox, click Delete. Then use
"Add a resume to a profile" (or just open Resume Clinic and upload there)
to re-parse under the latest parser prompt (ADR-067).

---

## 2026-05-28

### Changed — Preserve full resume fidelity at parse time (ADR-067)

A Resume Clinic export of an early-career profile surfaced two content-loss
bugs that turned out to be parser-layer, not renderer-layer: the source's
`GPA: 3.9/4.0`, `3x Presidents List and Deans List Scholar`, and the five
skill categories (`Security & Monitoring`, `Networking & Protocols`,
`Security Tools`, `Scripting & Operating Systems`, `Cloud & Collaboration`)
were dropped because the `ResumeProfile` schema had no slot for them. ADR-067
extends the schema additively to preserve them end-to-end.

- **Schema additions** (`app/schemas/resume_profile.py`):
  - `EducationEntry.gpa: str | None` (as-written, e.g. "3.9/4.0").
  - `EducationEntry.honors: list[str]` (free-text awards, dean's list, etc.).
  - New `SkillGroup` model = `{category: str, skills: list[str]}`.
  - `ResumeProfile.skill_groups: list[SkillGroup]` — the categorised view.
  - The flat `ResumeProfile.skills` list is kept (the Scoring Agent and
    keyword filters read it). When `skill_groups` is populated, `skills`
    is derived as the union (first-seen-order, de-duplicated).
- **`_ResumeEnhancement`** (the LLM-output schema in
  `app/providers/claude_provider.py`) gets the same fields so the parser's
  Sonnet enhancement call can return them.
- **Parser prompt v2** (`app/prompts/agents/resume_parser.txt`): asks for
  `gpa`, `honors`, and `skill_groups` explicitly, with the "extract verbatim,
  never invent, empty when absent" rule applied.
- **Parser pass-through** (`app/services/resume_parser.py`): when the LLM
  populates `skill_groups` but not the flat `skills` list, the parser
  derives the flat list from the groups (preserves the Scoring path).
- **Renderer** (`app/services/resume_text_renderer.py`): every format
  (Markdown, plain text, HTML, JSON Resume, DOCX, PDF) now reads the new
  fields. GPA and honors render under each Education entry; skills render
  as a categorised list when `skill_groups` is populated, falling back to
  the flat list otherwise. Resumes parsed BEFORE this change render exactly
  as before (the new fields default to empty/None on old rows).
- **Tests**: 11 new ADR-067 tests covering composer pickup, the markdown /
  HTML / plain-text / JSON / DOCX / PDF renderers with grouped skills and
  GPA + honors, plus the flat-skills fallback for stale rows. Full suite
  at 675 (was 664; +11).
- **ADR-067**:
  `docs/architecture/adr/ADR-067-preserve-resume-fidelity-at-parse-time.md`
  (decision + tradeoffs + non-goals); ADR index updated.

**How to get the new fields populated on your existing resume:** re-upload
the PDF. The parser cache is keyed by raw_text hash, so the cached profile
returns unchanged for the same file - to force a fresh parse with the new
prompt, upload again with a different filename or modify the source PDF.

---

## 2026-05-28

### Added — Resume Clinic: text/file export in six formats (ADR-066 nice-to-have)

The "later nice-to-have / full resume-text export" that ADR-066 named is now
live. Deterministic — no LLM call. Decision-aware: `approve` applies the
agent's overhaul, `edit` uses the human's final draft, `reject` falls back to
the original resume, `revise` / undecided renders a preview banner.

- **Renderer** (`app/services/resume_text_renderer.py`). One canonical
  composer (`compose_resume`) materialises a profile + overhaul + decision
  into a `RenderedResume` intermediate; six format-specific render
  functions (`render_markdown`, `render_plain_text`, `render_html`,
  `render_json_resume`, `render_docx`, `render_pdf`) walk that
  intermediate. Fidelity contract: nothing the agent didn't rewrite is
  touched; placeholders like `[N]` / `[X]%` survive verbatim; unmatched
  rewrites are appended (never silently dropped); rewrites match
  bullets by `section_label` + exact-then-substring on `original_text`.
- **Formats**:
  - Markdown (canonical, no dependency)
  - Plain text (ATS-friendly, hard-wrapped at 72 chars)
  - HTML (hand-rolled, no extra dependency; HTML-escaped inputs)
  - JSON Resume (jsonresume.org schema subset)
  - DOCX (python-docx; one-page-friendly margins, Calibri 11pt)
  - PDF (reportlab; LETTER size, Helvetica family)
- **Dependencies**: `python-docx>=1.1.0` and `reportlab>=4.0.0` (both
  pure-Python; no system deps; added to `requirements.txt`).
- **REST endpoint**:
  `GET /resume-clinic/{id}/export?format=md|txt|html|json|docx|pdf`.
  Returns raw bytes with the right `Content-Type` and a
  download-friendly `Content-Disposition: attachment; filename=...`.
  400 on unsupported format; 404 on unknown clinic review or missing
  resume.
- **UI** (Resume Clinic view). New "Export the final resume" panel
  beneath the decision controls: format selectbox, inline preview for
  text-y formats (md / txt / json / html), and a download button.
- **Tests**: 31 renderer tests (composer + decision logic + each
  format) + 11 endpoint tests (per-format success, default format,
  unknown format 400, unknown review 404, decision-aware rendering
  for approve vs reject). Full suite at 664 (was 622).

---

## 2026-05-28

### Added — Resume Clinic: standalone, job-agnostic resume review (ADR-066)

The second product surface — a profile-scoped, out-of-graph resume tool that
runs on the resume alone. No discovery, no scoring, no JD. Built ahead of
Article 10 so the senior-tuned funnel no longer gates resume-facing help
behind a qualified job.

- **Schema + repository** (`resume_clinic_reviews` — 20 tables total).
  Columns: `id`, `user_id`, `resume_id`, `workflow_run_id`, `target_role`,
  `target_track`, `seniority_aware`, `review_json` (quality scorecard),
  `alignment_json` (nullable role/track alignment), `overhaul_json`
  (reorganization + rewrites), `fidelity_review_json`, `decision`,
  `edited_json`, `decided_at`, `created_at`. `ResumeClinicRepository`
  mirrors the tailoring repo shape (create / get_by_id / list_by_user /
  set_decision).
- **Resume Reviewer agent** (`AGENT_NAME="resume_reviewer"`,
  `claude-sonnet-4-6` default; pinned in `tests/model_pins.json`).
  `ResumeClinicReview` schema with `quality` (Literal-enforced dimensions),
  `alignment` (nullable), `reorganization`, and `rewrites` in the tailoring
  claim-type shape so the Fidelity Reviewer and the existing renderer can
  reuse most plumbing. Prompt at `app/prompts/agents/resume_reviewer.txt`
  with the seven dimensions, evidence-binding rule, seniority-aware mode,
  and optional role-data grounding block.
- **Out-of-graph runner** (`app/services/resume_clinic_runner.py`).
  `run_clinic(user_id, resume_id, *, target_role, target_track,
  seniority_aware, ...)` chains resume load -> ownership check ->
  lightweight `workflow_runs` row -> `RoleDataProvider.lookup` ->
  reviewer -> `FidelityReviewer` on `rewrites` -> persist. The Fidelity
  invariant is enforced in the runner; the row carries a `clinic:<id>`
  synthetic `job_id` so the fidelity prompt's expectations work
  unchanged.
- **Pluggable `RoleDataProvider`** (`app/services/role_data/`). v1 ships
  `NullRoleDataProvider`; ESCO and O*NET providers are fast-follow
  (ADR-066 Decision G). `lookup` MUST NOT raise — graceful fallback to
  LLM-only is the contract.
- **REST API** (`app/api/routers/resume_clinic.py`).
  `POST /users/{id}/resume-clinic`, `GET /users/{id}/resume-clinic`,
  `POST /resume-clinic/{id}/decisions`. Shared decision validator
  (`app/api/decision_validation.py`) extracted so clinic + tailoring
  use the same approve/revise/reject/edit payload shape. Edit carries
  the human draft; not re-reviewed (ADR-059).
- **Streamlit "Resume Clinic" view** (`app/ui/streamlit_app.py`). Resume
  picker, target role/track, seniority-aware toggle. Results pane renders
  quality scorecard, alignment, reorganization plan, side-by-side
  rewrites with claim-type chips + evidence captions, fidelity verdict.
  Approve / Revise / Reject decision controls. Past-runs panel via
  `db_reader.load_user_clinic_reviews`.
- **Docs**: ADR-066 + the implementation walkthrough, `data_model.md`
  (new §4.9.1, 19 -> 20 tables), `api_reference.md` (new Resume Clinic
  section), `agent_model.md` (new §13.1 Resume Reviewer), `ui_model.md`
  (new §6.13), CLAUDE.md (agents table + Resume Clinic rules block),
  `docs/wiki.md` (20-table note).
- **Tests**: 9 repo + 16 agent + 14 runner + 13 router = 52 new unit
  tests; full suite at 622 passing (was 570 before the build began).
- **Not yet built**: live integration tests for the clinic, the E2E
  validation notebook (`notebooks/resume_clinic_validation.ipynb`),
  clinic-tuned Fidelity Reviewer prompt, inline edit-with-payload UI,
  ESCO/O*NET role-data providers.

---

## 2026-05-26

### Added — Edit an existing profile (rename / note) + add a resume to an existing profile

- `PUT /users/{id}` updates a profile's display name / note (`UserRepository.update`; whitespace-only note stored as null; 404 unknown, 422 blank name). The id is never changed.
- The **Profiles** view gains a "Manage an existing profile" area: an "Edit a profile (name / note)" expander and an "Add a resume to a profile" expander (the latter wires the existing `POST /users/{id}/resume` endpoint to a UI control — previously only reachable from the new-profile onboarding wizard).
- Search criteria / experience window / threshold remain editable per profile via Start New Run's "Save these settings as my defaults".
- Docs: `api_reference.md`. Tests: new PUT /users cases in `test_api_users.py`.

### Added — Experience-targeted discovery: years-of-experience cap + senior exclusion (ADR-065)

Per-profile, opt-in levers so an entry-level profile can target early-career roles. All off by default (Primary unaffected).

- **Years-of-experience window (`search.max_years_experience` / `search.min_years_experience`).** A deterministic regex (`app/services/experience_filter.py`) parses each posting's description ("5+ years", "3-5 years", "minimum of 2 years", "entry level"/"new grad" -> 0); `JobDiscoveryService.discover()` drops postings outside the window. The max cap compares the JD's lowest stated bar (`exceeds_cap`); the min floor compares its highest stated bar (`below_floor`, so "2+ years, 5+ years preferred" survives a min-5 floor). Both keep postings with no detectable experience (mirrors salary's ignore_if_missing). No LLM cost. `0`/`None` = that bound off; default cap for a new entry-level profile is 2. The min floor (for senior profiles excluding junior roles) is noisier since many JDs omit a stated floor.
- **Senior exclusion (`search.exclude_senior`).** When on, the per-run Adzuna search passes a curated `SENIOR_TERMS` set as Adzuna's `what_exclude` (drop at the source; `AdzunaScraper` now emits `what_exclude`) and adds the same terms to the per-run title gate.
- Wiring: `discover_jobs` reads both from `effective_config.search`, passes the cap to `discover()` and `exclude_senior` to `adzuna_scraper_factory(roles, locations, exclude_senior)`. Both knobs are on the Start New Run form and persist as profile defaults via "Save these settings as my defaults".
- Scoring stays senior-tuned (ADR-064 Decision C deferred). Docs: ADR-065 + index, CLAUDE.md, config_model, workflow_model, user_guide, config.example. Tests: 562 passed (new `tests/v2/test_adr065_experience_filter.py`).

### Added — Per-profile search criteria drive discovery; role-derived relevance (ADR-064)

A profile's own roles/locations now actually drive auto-discovery, so a second profile (e.g. an entry-level cybersecurity new-grad) can search its own roles instead of profile 0's senior titles.

- **Discovery honors `search_criteria`.** When a run carries `roles`, `discover_jobs` builds a per-run Adzuna scraper via `WorkflowDependencies.adzuna_scraper_factory(roles, locations)` and passes `skip_builtin_adzuna=True` so the senior startup Adzuna is omitted. No roles -> built-in startup scraper (backward compatible). The scraper's title-relevance gate is now overridable (`AdzunaScraper(..., relevant_keywords, excluded_keywords)`); the per-run search derives relevance from the role tokens (`relevance_tokens()`), so non-senior titles ("Security Analyst", "SOC Analyst") survive the gate that otherwise requires senior keywords.
- **Primary's criteria tied to profile 0.** `search.titles` / `search.locations` were already stored under user 0 in `user_config`; the mangled `search.locations` (comma-split had shattered "Atlanta, GA" into "Atlanta" + "GA") was repaired, and the Start New Run + onboarding location inputs are now **one-per-line** so "City, State" is preserved. "Remote" on its own line triggers the remote search.
- **Scoring stays senior-tuned (ADR-064 Decision C, deferred).** Entry-level scores are modest by design; `scoring.min_match_score` (already per-profile) is the lever. A persona-aware rubric is out of scope.
- Docs: ADR-064 + index, CLAUDE.md scraper rules, `workflow_model.md`, `config_model.md`, `user_guide.md`. Tests: 549 passed (new `tests/v2/test_adr064_discovery.py`).

### Added — Multi-user profiles with a single swappable identity seam (ADR-062)

The app can now serve more than one job-seeker from one install — each profile with its own resume, search defaults, config and per-agent model overrides, learned memory, cost view, and history. Built as the simplest front door (a no-auth profile selector) that does not foreclose real authentication later: the expensive, hard-to-reverse work (the data model) is done once and is identical regardless of the eventual auth model.

- **Identity anchor.** New `users` table (`id INTEGER PK`, `name`, optional human-only `note`, `created_at`). `id 0` is seeded by the migration as the owner of all pre-existing data; profiles created via `POST /users` auto-increment from `1`. Reference columns (`workflow_runs.user_id`, `user_config.user_id`, new `resumes.user_id`, `memory_items.user_id`) standardize on the decimal-string form (`"0"`, `"1"`, ...) so comparisons stay string-to-string.
- **Single identity seam.** `app/api/identity.py::get_current_user_id` resolves a `?user_id=` query parameter (no HTTP headers), defaults to `"0"` (backward compatible), and validates against `users`. Every router depends on it; nothing parses identity ad hoc. The UI mirror is `api_client.set_user_id` (attaches the param) + `db_reader` (filters by it). Adding auth later changes only this one function body.
- **Per-user scoping.** Resumes are per-user active (creating a resume deactivates only that profile's prior ones); memory is isolated per profile; `register_run` writes the run owner; config collapses to two layers (`yaml -> user_config per-user`, the legacy `user_id IS NULL` system-wide layer migrated to `"0"`). Per-agent model/provider overrides ride the per-user config layer (rebuild-on-switch under sequential use).
- **UI.** Sidebar profile selector; a 3-step "Add profile" onboarding wizard (identity -> resume upload -> default roles/locations, steps 2-3 skippable); Start New Run's free-text Resume ID box replaced by a picker over the active profile's resumes; Cost Dashboard gains a per-profile / system-wide toggle. History and cross-run analytics read only the active profile's data (orphan/legacy rows COALESCE to `"0"`).
- **Isolation is cooperative, not enforced** (no auth): the selector decides *which* data a request touches, it is not an access boundary. No ownership-authorization checks were added — they are meaningful only once identity is authenticated, and the seam is exactly where they attach later (ADR-062 Decision E; `security.model.md` 4.1).
- **Migration.** Timestamped backup taken first; `init_db` additively creates `users` + the new `user_id` columns and indexes, seeds user 0 (guarded on `id=0`), and backfills all pre-existing `resumes`/`memory_items`/`workflow_runs`/`user_config` rows to `"0"`. Idempotent and additive (no drops, no data loss).
- New endpoints: `GET /users`, `POST /users`, `POST /users/{id}/resume` (onboarding resume upload).
- Docs: ADR-062 + index, `data_model.md` (users table, per-user columns, indexes, corrected "always null" notes), `config_model.md` (two-layer per-user merge), `api_reference.md` (`/users` + identity query param), `state_and_memory_model.md` (per-user memory isolation), `security.model.md` (cooperative-isolation note), `CLAUDE.md` (identity/multi-user invariants).

Tests: 599 passed (new `tests/v2/test_api_users.py` resume-upload cases, `test_cost_user_scoping.py`, `test_db_reader_user_scoping.py`; earlier phases added `UserRepository`, identity-seam, and per-user repo/config tests).

### Removed — Retire the v1 reference runtime (ADR-063)

The v1 runtime is removed now that v2 is the only system in use; the v1 modules v2 imports are kept and reframed as shared libraries.

- **Deleted:** `main.py`, `dashboard.py`, `agents/`, `storage/`, `claude/`, `prompts/`, `scrapers/ladders.py`, `models/profile.py`, and the two tests that exercised removed code (`tests/test_db.py`, `tests/test_filters.py`).
- **Kept as shared libraries:** `scrapers/{base,adzuna,linkedin}.py` (the Adzuna scraper is wrapped by `ConcurrentAdzunaScraper`; LinkedIn is built in `dependencies.py`) and `models/{job,config_schema,filters}.py` (job schema + `AdzunaConfig` + keyword filters used by `JobDiscoveryService`). The `scrapers/__init__.py` and `models/__init__.py` exports dropped the removed siblings.
- **Config trim:** `config.yaml` / `config.example.yaml` reduced to the v2-only sections actually read — `search`, `scrapers.adzuna`, `retention`, `agents`, `models`. Dropped the v1/inert blocks (`claude`, `tracks`, `storage`, `scrapers.linkedin`/`ladders`, `salary`, `staleness`, `search.work_mode`/`keywords`, plus the unread v2 `llm`/`limits`/`scoring`-threshold/`tailoring` blocks). The now-unbacked salary/staleness knobs were removed from the Settings UI.
- Reverses the "keep v1 stable for reference" half of ADR-001; the retired runtime stays recoverable from git history.
- Docs: ADR-063 + index; CLAUDE.md "Shared libraries from v1" note; `docs/README.md` and `docs/wiki.md` v1-reference sections reframed as retired/historical.

Tests: 542 passed (599 minus the 57 in the two removed v1 test files; no v2 tests affected).

---

## 2026-05-24

### Added — Configurable funnel width + on-demand deep review and interview prep (ADR-061)

The discover -> score -> tailor -> interview funnel is now user-steered within hard cost ceilings, and any scored job (not just the auto-selected top-3) can be carried to the narrow end.

- **Configurable caps.** New config keys `scoring.max_scored` (default 10, ceiling `MAX_SCORED_CEILING=25`) and `search.max_discovered` (default/ceiling `MAX_DISCOVERED_JOBS=50`), merged the standard three-tier way (yaml -> user_config system-wide -> per-run `effective_config`). `app/workflows/limits.py` gains `get_max_scored()` and a rewritten `get_max_discovered_jobs()` that clamp to the ceiling; `ConfigService._enforce_limits` clamps the merged config and `_SYSTEM_MAX_JOBS` was raised 20 -> 50 so the discovery-service backstop no longer throttles the manual-mode wide net. `search.max_jobs` retired as a user-facing knob (now an internal backstop). Settings + Start New Run UI expose the two new knobs.
- **Ad-hoc tailoring for any scored job.** UI picker widened from `selected_jobs` to `scored_jobs`. The single-job critic+auditor reflection loop was extracted to `app/services/deep_review_runner.py::review_one_job` (shared by the `deep_review` node and the new endpoint). `POST /workflows/{wf}/jobs/{job}/tailorings` gains `auto_deep_review` (default true): a scored-but-unreviewed job is deep-reviewed first.
- **New out-of-graph endpoints.** `POST /workflows/{wf}/jobs/{job}/deep-review` and `POST /workflows/{wf}/jobs/{job}/interview-prep`, plus a per-job "Prep for interview" button in Workflow Detail. Both follow the ADR-055 out-of-graph shape; no `interrupt()` is introduced (ADR-059 property preserved). `MAX_LLM_CALLS_PER_RUN=200` stays the absolute backstop.
- Docs: ADR-061 + index, `config_model.md` (funnel-width keys), `api_reference.md` (two new endpoints + `auto_deep_review`), `workflow_model.md` (new Mermaid funnel diagram + auto-selection/on-demand sections), `wiki.md` (corrected limits table + ADR list 057-061), `architecture_overview.md`, `hitl.md`, `patterns.md`, `agent_model.md`, cost docs, `CLAUDE.md` invariants.

Tests: 562 passed (new `tests/v2/test_funnel_limits.py`; cap-clamp, deep-review-on-demand, and interview-prep router tests; updated tailoring/config tests for the new defaults).

---

## 2026-05-23

### Added — Manual scoring selection: widen discovery, score only the selected (ADR-060)

Opt-in human triage step between discovery and scoring. Research + scoring was being paid on every discovered job, including the many a human discards on sight and that fall below threshold anyway. This is a targeted cap (score only what is kept) replacing ADR-052's blunt cap (narrow discovery), so the net can widen again at lower spend.

- When `effective_config.scoring.manual_selection` is true (default off), discovery casts a wider net (`MAX_DISCOVERED_JOBS=50`) and the graph parks at a new `await_scoring_selection` node (status `awaiting_scoring_selection`) WITHOUT scoring.
- `POST /workflows/{id}/scoring` re-enters the SAME graph/thread at `score_jobs` via a conditional entry point (`phase="scoring"`) and scores only the selected subset (capped at `MAX_JOBS_PER_RUN`), then continues auto-select -> deep review -> report. One `workflow_id`, two phases.
- Shape: out-of-graph / phased (no `interrupt()`), preserving ADR-059's "graph runs end to end" property — the human choice sits between two phases, like out-of-graph tailoring (ADR-055).
- `app/workflows/{limits.py,routers.py,graph_state.py,workflow_graph.py}`, `app/workflows/nodes/{await_scoring_selection.py,discover_jobs.py}`, `app/api/routers/workflows.py`, UI kickoff toggle + selection screen + `api_client`. Auto mode unchanged.

Tests: 546 passed (phase-1 parks without scoring; phase-2 same-thread re-entry scores only selected; endpoint 404/409/422/happy-path).

### Added — Human `edit` decision on the tailoring path (ADR-059 decision 2)

The out-of-graph tailoring decision model gained an `edit` verb alongside approve / revise / reject. An edit is an acceptance with the user's own wording: it carries the human-authored draft, flips `approved=1`, and is recorded as owner-authored. Per the ADR a human edit is trusted as final and is NOT re-run through the Fidelity Reviewer — the reviewer polices the agent, not the accountable human. The agent's original draft is retained in `tailored_json`; the human draft lands in a new `edited_json` column.

- `TailoringDecisionRequest` gains `edit` + an `edited` body (validator requires `edited` when `approval == "edit"`); `TailoringRepository.set_decision(edited=...)`; additive `edited_json` column with a try/except ALTER migration; `TailoringResponse` surfaces `edited`; UI edit-and-accept-as-final affordance on the tailoring card.
- Fixed: the global `RequestValidationError` handler now wraps `exc.errors()` in `jsonable_encoder` (a custom validator can put a non-serializable exception in `ctx` that bare `exc.errors()` failed to encode — a latent bug the new validator surfaced).
- UI follow-up (commit 582dc31): a "Before you decide" consequence summary above the approve/edit/revise/reject controls (reviewer recommendation + confidence, unresolved fidelity-flag count, one-line consequence per choice). Pure presentation, no schema/API change.

Tests: 540 passed (two new edit-decision cases).

### Changed — Retire the dead in-graph HITL subsystem (ADR-059 decision 1)

Job-selection HITL was already removed (auto-select) and tailoring approval moved out-of-graph (ADR-055), which left the in-graph tailoring node, the `await_tailoring_approval` interrupt, the tailoring routing shim, `POST /workflows/{id}/decisions`, the JobSelection/Tailoring decision schemas, and the `pending_decision` / `user_requested_tailoring` state fields all unreachable. Removed: the graph now runs end to end with no `interrupt()`. The only remaining HITL pattern is the out-of-graph curate-after tailoring decision. `human_decisions` (the auto-select audit trail) is kept. Docs reconciled: `CLAUDE.md`, `api_reference.md`, `hitl.md` (status banner), `workflow_model.md`.

### Changed — Model catalog, pricing, and default assignment move to YAML (ADR-058)

Supersedes the in-code catalog and default-assignment constants of ADR-053. The model catalog, per-million-token pricing, and default per-agent `(provider, model)` assignment now live in `config/config.yaml` (`models:` and `agents:` blocks) — editing that file is how the system learns about new models or prices, no code release. The cost-cap allowlist (`HIGH_VOLUME_SAFE_MODELS`) stays in code as a policy boundary. Per-workflow assignment is snapshotted at kickoff with an override hook (runtime per-workflow swap remains future work). Touches `model_registry`, both providers, dependency wiring, the config router, and `config.example.yaml`.

Tests: 538 passed.

---

## 2026-05-05

### Changed — Cost cuts for the high-cost agents (advisor + coach + tailoring)

Audit data showed the four cost-driving agents per run were `tailoring_agent`, `interview_coach`, `career_advisor` (each ~$0.07-0.09 per Sonnet call), and `scoring_agent` (10× Haiku). Two coordinated cuts that are quality-neutral.

- `app/services/context_trimmer.py` — new module with pure trimming functions: `trim_resume_profile`, `trim_review`, `trim_career_advice`, `trim_score`. Each drops fields downstream agents don't read (raw_text, section_reviews, suggested_improvements, questions_for_user, verbose advice prose). Per-field justification documented in the module docstring.
- `app/api/routers/tailoring.py`, `app/workflows/nodes/career_advice.py`, `app/workflows/nodes/interview_prep.py` — wired the trim functions into the context payload before each agent call. Saves 1-3K input tokens per call across the three high-cost agents.
- `app/providers/prompt_loader.py` — `assemble()` now supports a second cached system block. When the context dict contains a `_cached` key, that sub-dict is moved to a separate `SystemMessage` with `cache_control: ephemeral`. Anthropic's prompt-cache 5-minute TTL means subsequent calls in the same session pay 10% on the cached block.
- The three call sites move `resume_profile` (the largest static-per-session chunk) into `_cached`. Resume profile is constant across all tailoring/advisor/coach calls in a session — the cache hit rate should be high.
- `tests/v2/test_context_trimmer.py` — 12 new tests pin every per-field decision (so a future refactor that adds a field to one of the trimmed dicts doesn't accidentally re-bloat the context) and verify the PromptLoader cached-block plumbing (3-block emission when `_cached` set; 2-block backwards-compat when absent; empty `_cached` is skipped).

Estimated impact per run with 1 selected job + 1 tailoring draft:
- Before: ~$0.27 (advisor $0.07 + coach $0.09 + tailoring $0.09 + scoring $0.03)
- After (Phase 1): ~$0.17-0.20 once cache is warm; ~$0.22 on cold cache
- $25 budget multiplied from ~92 runs to ~125-150 runs

Tests: 518 passed (was 506), 1 skipped.

### Fixed — ResearchContext rejected Haiku stringified-list emissions

Same root cause class as the recent ResumeReview fix: after the Sonnet → Haiku cost cut, the smaller model's emission shape is less reliable. Reported error in production:

    leadership_signals
      Input should be a valid list [type=list_type,
      input_value='["Head of" title indicat...CTO or VP Engineering"]', input_type=str]

Haiku returned a JSON-encoded STRING (`'["Head of...", "CTO..."]'`) where the schema expected a real `list[str]`. Schema-repair retry hit the same issue and the workflow crashed.

- `app/schemas/research_context.py` — added `field_validator(mode="before")` covering all four signal lists (`technology_signals`, `leadership_signals`, `domain_signals`, `risk_flags`). The validator detects a string value, JSON-decodes it if it looks like an array, falls back to wrapping a non-JSON string in a one-item list, and treats empty strings as `[]`.
- Same tolerance pattern as ResumeReview: signal lists, `research_steps`, and `confidence` now default to empty/0. Load-bearing fields (`job_id`, `company_summary`, `role_context`) stay required.
- `app/prompts/agents/research_agent.txt` v1 → v2 — explicit "Output Schema" section enumerating every field with its purpose. Adds a CRITICAL block contrasting `["x", "y"]` (correct array) vs `"[\"x\", \"y\"]"` (wrong; stringified) with concrete examples to nudge Claude away from the stringified emission.
- `tests/v2/test_schemas.py` — 6 new tests pin the coercion: jsonish-string → real list, all 4 signal fields covered, non-JSON string wrapped in one-item list, empty string becomes empty list, minimal partial response works, load-bearing omissions still rejected.

This is a follow-up to the cost-cut Haiku migration, not a reversal — the cost win holds. The schema is now appropriately tolerant of Haiku's emission quirks.

Tests: 506 passed (was 500), 1 skipped.

### Added — Live config reload eliminates restart-to-apply friction (ADR-053 addendum)

Symptom that triggered this: user saved `scoring_agent: Haiku` in Settings, ran a workflow, and got billed for Sonnet because the backend was still running with the previous binding. Per the original ADR-053, a Settings save required a manual `uvicorn` restart to take effect — easy to forget, and real money when forgotten.

- `app/api/dependencies.py` — new `reload_deps_and_graph()` function. Atomically rebuilds `WorkflowDependencies` + the compiled graph from current `user_config` (re-reads `ModelRegistry`, re-instantiates agents, re-compiles graph). Releases the old SqliteSaver only after the new graph is wired so `get_graph()` never returns None during the swap.
- `app/api/routers/config.py` — new `POST /config/reload` endpoint. Calls `reload_deps_and_graph()`, returns the new effective `agent_assignment` so the caller can confirm the change took effect.
- `app/ui/api_client.py` — `reload_config()` wrapper.
- `app/ui/streamlit_app.py` — Settings page's `_save()` helper now calls `reload_config()` after every successful `put_config()`. Toast shows the new active assignment for `agents.*` keys: "Saved + applied. Active: scoring_agent → claude/claude-haiku-4-5-20251001". Caption text updated to reflect "no restart needed."
- `docs/architecture/adr/ADR-053-pluggable-per-agent-provider-and-model-selection.md` — addendum 2026-05-05 documenting the reload path, what it picks up, and what still requires a real restart (prompt file changes, code changes).
- 3 new tests in `tests/v2/test_api_config.py`: endpoint contract, error envelope on rebuild failure, idempotency.

In-flight workflows are not disturbed — they hold a reference to the old graph and run to completion on the old assignment. Only NEW workflows pick up the change. Same semantics as a real restart, just without process exit.

What still requires a process restart:
- Prompt file changes (PromptLoader caches at first read)
- Code changes
- Provider client init bugs that need a fresh interpreter

Tests: 500 passed (was 497), 1 skipped.

### Fixed — Observability gap: llm_calls and run_metrics tables were never populated

Diagnosis triggered by hitting the Anthropic credit ceiling. Found that despite ~$20 of API spend reported by the provider, the local `llm_calls` table had 0 rows and `run_metrics` had 0 rows — so per-call cost attribution was impossible to reconcile against the billing console. Two real wiring bugs:

- `app/agents/base_agent.py::_run` called `log_agent_started`, `log_agent_completed`, and `log_agent_failed` — but never `log_llm_call`. Every Claude / OpenAI call happened invisibly to the audit trail.
- `app/workflows/nodes/register_run.py` never called `create_run_metrics`. `app/workflows/nodes/generate_report.py` never called `finalize_run_metrics`. Both methods existed in `ObservabilityService` but were only exercised in tests.

Fix:

- `app/providers/llm_client.py` — `LLMClient` ABC gains `provider_name` and `model_name` properties (default-implemented as `"unknown"` for back-compat with bare-bones test doubles). `ClaudeProvider` and `OpenAIProvider` override to return `"claude"`/`"openai"` and `self._model_name`.
- `app/agents/base_agent.py` — `_run` now brackets the LLM call with its own `time.monotonic()` and calls `self._observability.log_llm_call(...)` after every successful `complete_with_usage`. Failures in the logging path are swallowed (observability must never crash a run).
- `app/services/observability_service.py` — adds `init_run_metrics(workflow_id, started_at)` and `compute_run_totals_from_llm_calls(workflow_id) -> dict`. The latter is the truth source for finalize: it queries `llm_calls` rather than trusting the lossy in-memory `state["run_metrics"]` aggregator.
- `app/repositories/observability_repository.py` — adds `get_llm_calls_by_run(workflow_id)`.
- `app/workflows/nodes/register_run.py` — accepts an optional `ObservabilityService`; calls `init_run_metrics` after the workflow_runs row is persisted.
- `app/workflows/nodes/generate_report.py` — calls `compute_run_totals_from_llm_calls` then `finalize_run_metrics` after the terminal-state write. Per-run cost is now reconciled from the audit trail, not from in-memory estimates.
- `tests/v2/test_observability_wiring.py` — 5 new tests lock in the fix: every `BaseAgent.run()` writes one `llm_calls` row; multiple runs produce multiple rows; observability failures don't crash the agent; `register_run` creates the `run_metrics` row; `generate_report` finalizes it from `llm_calls` rather than state_json.

### Changed — Cost cuts: resume_critic moved to Haiku; MAX_SELECTED_JOBS 10→3; MAX_REVIEW_ROUNDS 3→2

Per-agent breakdown of the credit-blow run (`55548473`) showed `resume_critic` on Sonnet was ~80% of run cost (16 calls × Sonnet rates). Three cuts that together turn a worst-case run from ~$1.40 down to ~$0.30-0.40, multiplying the $25 budget into roughly 60-80 runs instead of ~18.

- `app/providers/model_registry.py` — `resume_critic` default moved from `claude-sonnet-4-6` to `claude-haiku-4-5-20251001`. The Review Auditor loop already polices critic output, so the quality risk of dropping to Haiku is bounded. Override per-run via Settings if a specific session warrants Sonnet.
- `app/workflows/limits.py` — `MAX_SELECTED_JOBS` lowered from 10 (ADR-054) to 3. ADR-054's design intent — every qualifying job reaches deep review — still holds; this only changes the cap on how many qualifying jobs we'll pay for per run.
- `app/workflows/limits.py` — `MAX_REVIEW_ROUNDS` lowered from 3 to 2. The reflection loop usually converges by round 2 in observed runs; round 3 rarely changes the verdict.
- `CLAUDE.md` Key Invariants section updated to reflect the new caps with rationale.

Tests: 475 passed (was 470), 1 skipped.

### Added — Per-job exclusion as a pipeline filter (ADR-057)

Restored v1's per-job exclusion as a deliberate filter primitive (NOT application tracking). v1 had `excluded` / `excluded_reason` columns on the `jobs` table; v2 dropped them along with the actual application-tracking surface (Apply / Save / status), conflating two distinct concerns. ADR-057 makes the distinction explicit: this is a filter the user gives the system, not an outcome the system records.

- `app/repositories/database.py` — three columns added to `jobs` via try/except `ALTER TABLE` in `init_db()` (same migration pattern ADR-055 used for `tailored_resumes`):
  ```sql
  excluded INTEGER NOT NULL DEFAULT 0
  excluded_reason TEXT
  excluded_at TEXT
  ```
- `app/repositories/job_repository.py` — `set_excluded(job_id, reason)`, `clear_excluded(job_id)`, `excluded_set() -> set[str]`, `list_excluded() -> list[dict]`. `upsert()` left alone — re-discoveries of the same `job_id` preserve the prior flag because the `ON CONFLICT(id) DO UPDATE` clause only overwrites `normalized_job_json`.
- `app/api/routers/jobs.py` — new `exclusion_router` with `POST /jobs/{job_id}/exclude`, `DELETE /jobs/{job_id}/exclude`, `GET /jobs/excluded`. Wired in `app/api/main.py`.
- `app/services/job_discovery_service.py` — `deduplicate()` already drops URLs that exist in the DB; comment added documenting that this implicitly filters re-discoveries of excluded URLs (the cost-saving claim from ADR-057). No new logic needed at discovery time.
- `app/ui/db_reader.py` — `load_scored_jobs(include_excluded=False)` (cross-run analytics; default-hide) and `load_workflow_jobs(workflow_id, include_excluded=True)` (per-run Find & Score; default-show with a column for the badge). Both return the new `excluded` / `excluded_reason` / `excluded_at` columns.
- `app/ui/api_client.py` — `exclude_job(job_id, reason)` and `unexclude_job(job_id)`.
- `app/ui/streamlit_app.py` — Find & Score table gains a 🚫 badge column, single-row selection, and a per-row `🚫 Exclude selected` / `♻ Un-exclude selected` button. Sidebar adds an `Include excluded jobs` checkbox that threads through every cross-run analytics view (Top Matches, IC / Architect / Management Track, Companies).
- `tests/v2/test_job_exclusion.py` — 14 new tests covering the repository, the router, the discovery filter, and the upsert-preserves-flag invariant.

What this ADR does NOT change:
- CLAUDE.md's "no application tracking features" rule still stands. Apply / Save / status remain out of scope.
- Tailoring decision flow is unrelated; those track decisions on TAILORED DRAFTS, not on jobs.
- Job-selection HITL remains removed (auto-select per ADR-054).

### Added — Directional per-track impact estimate for tailoring drafts (ADR-056 addendum #3)

User question: "Is it possible to estimate the score improvement after the suggested revision?" The honest answer is yes, but with three options that trade off cost vs precision vs self-fulfilling-prophecy risk. We chose Option A: a cheap, deterministic, structural derivation that tells the candidate WHICH career tracks the draft is moving toward, NOT what number the ScoringAgent would assign.

- `app/ui/streamlit_app.py` — new `_estimate_track_impact(draft)` helper. For each reword/emphasize bullet across headline + summary + experience, tokenizes suggested_text and original_text, computes the set difference, and intersects with curated per-track keyword buckets (technical / architecture / leadership). Maps the count to a signal: `neutral` (0 keywords added), `small_lift` (1-2 in a single bullet), `likely_lift` (otherwise). Counts `claim_type="remove"` as freed-space and `claim_type="gap"` as unclosed-gap.
- `_render_estimated_impact(draft)` renders an "Estimated impact (directional, not a re-score)" panel between the Strategy summary and the section diffs. Shows per-track signal with up to 4 example tokens and a footer for freed bullets / unclosed gaps. Caption explicitly tells the candidate this is heuristic and structural.
- Why not re-score: the same ScoringAgent that scored the original would now score text written specifically toward its rubric — partly real lift, partly tautology, plus run-to-run variance the candidate would read as precision. ADR-056 addendum #3 documents the reasoning at length.
- No schema change, no DB change, no extra LLM call, no prompt change. Pure UI-layer derivation. Old drafts get the panel automatically when re-rendered.

### Changed — Tailoring headline section + impactful strategy summary (ADR-056 addendum #2)

User feedback after using the v4 draft on real jobs: (1) the strategy summary still read as generic prose despite the length budget; (2) the headline (the positioning tagline below the candidate's name) was the highest-leverage real estate on the resume but was not tailored at all.

- `app/schemas/tailored_resume_draft.py` — `TailoredResumeDraft` gains `headline_suggestions: list[TailoredBullet]` (defaults to `[]` for backwards compat). Docstring documents the new `"headline"` section_label and the relaxed `+/- 3 words` length rule (the strict 0.85x..1.05x band is too narrow at the 5-15 word scale headlines occupy).
- `app/prompts/agents/tailoring_agent.txt` v4 → v5 — adds Headline as task #1 (highest leverage real estate), exempts headline from the "one strong verb, one sentence" rule (headlines are noun-style positioning labels), and rewrites the Strategy Summary section to enforce a 3-part structure: (1) positioning thesis sentence, (2) two-three concrete JD-anchored moves with active verbs, (3) optional sharp interview-prep line. Frames the field as the load-bearing artifact it actually is: "the candidate is making a career decision based on this summary."
- `app/prompts/agents/fidelity_reviewer.txt` v4 → v5 — accepts `"headline"` as a valid section_label, applies the relaxed +/- 3 word length rule for headlines, and rewrites the Strategy Summary check to flag specific failure modes: hedging openings, generic praise, generic moves with no named JD signal. Diagnostic flags like `"Strategy summary opens with hedging; needs positioning thesis"` land in required_revisions.
- `app/ui/streamlit_app.py` — `_section_order` puts `"headline"` first; `_section_display` renders it as `"Headline (positioning tagline)"`; `_render_tailored_sections` consumes the new `headline_suggestions` list with the same fallback behavior as summary/experience for older drafts.
- `docs/architecture/adr/ADR-056-tailoring-page-budget-and-section-grouping.md` — second addendum documenting the additions and the impact-first framing.
- No DB migration; backwards-compatible.

### Changed — Tailoring per-suggestion rationale + strategy summary (ADR-056 addendum)

User feedback on the v3 draft: candidate could not tell *why* a particular rewrite would land better with the hiring manager, and the draft as a whole had no narrative the candidate could carry into a cover letter or interview. Two additive, backwards-compatible fields fix this.

- `app/schemas/tailored_resume_draft.py` — `TailoredBullet` gains `impact_rationale: str = ""` (one short sentence, <=25 words; references a concrete JD signal, not generic phrasing praise). `overall_tailoring_notes` repurposed from terse note to draft strategy summary (3-5 sentences, <=120 words). Both are meta — page-budget rule does NOT apply; budgets are for the candidate's reading time.
- `app/prompts/agents/tailoring_agent.txt` v3 → v4 — adds the Per-Suggestion Rationale section (with concrete good/bad examples) and the Strategy Summary section.
- `app/prompts/agents/fidelity_reviewer.txt` v3 → v4 — adds Rationale Quality Check (rejects generic phrasing, missing rationale) and Strategy Summary Check (non-trivial drafts must have a non-empty strategy summary).
- `app/ui/streamlit_app.py` — strategy summary rendered as a top-of-card `st.info` callout above all bullet diffs. Per-bullet rationale shown inline under the evidence caption with "💡 Why for this role:" prefix. Removed the old bottom-of-card "Notes:" line.
- `docs/architecture/adr/ADR-056-tailoring-page-budget-and-section-grouping.md` — addendum documenting the two field additions, the prompt version bumps, and the UI placement decisions. No new ADR; same contract spirit.
- No DB migration; all additions are backwards-compatible. Old drafts (no rationale, no narrative summary) render with the new fields silently omitted.

### Changed — Tailoring page-budget contract + section-grouped suggestions (ADR-056)

User feedback: tailoring suggestions were verbose enough that adopting more than a couple pushed the resume onto an extra page, and the flat `summary_suggestions` / `experience_bullet_suggestions` lists made it hard to map a suggestion back to the right resume section. Both made the feature lossy for the candidate it exists for.

- `app/schemas/tailored_resume_draft.py` — `TailoredBullet.claim_type` extended with `"remove"` (frees space by deleting a low-value bullet); new `section_label: str` field (defaults to `""` for backwards compat) carries identifiers like `"summary"`, `"experience:Acme:Staff Engineer"`, `"skills"`.
- `app/prompts/agents/tailoring_agent.txt` v2 → v3 — adds the per-bullet length band `ceil(0.85 * original_words) .. floor(1.05 * original_words)` so rewrites match the original line count instead of overflowing or collapsing; requires `section_label` per suggestion; documents `claim_type="remove"` as the way to free space.
- `app/prompts/agents/fidelity_reviewer.txt` v2 → v3 — mirrors the length-band check, validates `section_label` against the candidate's actual resume sections, and rejects `claim_type="remove"` with non-empty `suggested_text`. Layout violations land in `required_revisions` with diagnostic notes like `"Bullet N: 28w > 18w original"`.
- `app/ui/streamlit_app.py` — `_render_tailored_sections()` groups bullets by `section_label` in resume order (Summary → Experience entries in resume order → Skills) and shows per-section word-delta summary; `_render_one_bullet()` shows length delta inline (`24w → 19w (-5w)`) and renders `remove` / `gap` distinctly. Older drafts (no `section_label`) fall back into a single "Other suggestions" bucket per source list — re-running tailoring produces a properly grouped draft.
- `docs/architecture/adr/ADR-056-tailoring-page-budget-and-section-grouping.md` — new. ADR-015 and ADR-016 updated with pointers; index updated.
- No DB migration. The schema additions are backwards-compatible; the prompt version bump (v2 → v3) is what the observability pipeline keys off to distinguish drafts produced under the new contract.

Per-suggestion accept/reject and iterative-revision context (carrying user decisions from one draft to the next) are intentionally **not** included here; they are scoped for a follow-up ADR that builds on this section-labeled structure.

---

## 2026-05-03

### Fixed — Phase validation notebooks repaired after refactor drift; all 7 run green in mock mode

The seven `notebooks/phase_*_validation.ipynb` files had drifted against current code from accumulated refactors (ADR-053 ModelRegistry, ADR-054 auto-select for deep review, ADR-055 out-of-graph tailoring, the `complete_with_usage` typed return migration, the `WorkflowDependencies.resume_repo` addition, and a Windows portability bug in `ConfigService`). End-to-end execution in mock mode failed across phases 1, 3, 5, 6, 7. Fixed:

| File | What changed |
|---|---|
| `app/services/config_service.py` | YAML loader now opens with `encoding="utf-8"` (was the OS default — broke on Windows because `config/config.example.yaml` contains UTF-8 chars at byte 2). Real codebase bug surfaced by phase_1. |
| `notebooks/phase_1_validation.ipynb` | Updated stale assertions to current limits (max_selected_jobs 3 → 10, max_llm_calls_per_run 50 → 200; ADR-054). |
| `notebooks/phase_3_validation.ipynb` | Path detection robust to launch dir (cwd-or-parent). Cell that asserted `OpenAIProvider` raises `NotImplementedError` rewritten — OpenAIProvider is now a real LLMClient (ADR-053), constructed with a `PromptLoader` and a mocked `openai.OpenAI` client. |
| `notebooks/phase_4_validation.ipynb` | Path detection robust to launch dir. |
| `notebooks/phase_5_validation.ipynb` | Dropped `INTERVIEW_COACH_THRESHOLD` import (constant removed; threshold is now per-run via `get_min_match_score(state)` defaulting to `MIN_MATCH_SCORE_DEFAULT=75`). Added `resume_repo=MagicMock(spec=ResumeRepository)` to `WorkflowDependencies(...)` (now required). `build_phase` visualisation helper wraps `compile()` in try/except so converging-paths topologies still render via ASCII when LangGraph rejects parallel updates over plain-dict state. |
| `notebooks/phase_6_validation.ipynb` | Added `resume_repo` to `WorkflowDependencies(...)` and configured the mock to return a valid cached profile (default `MagicMock` returned a non-string for `parsed_profile_json`, causing `load_resume` to raise inside the graph background thread and leaving the workflow stuck in `running`). Cells 8-19 rewritten to drop the in-graph HITL #1 (`select_jobs_for_deep_review`) — replaced with a single end-to-end completion poll plus a markdown note pointing at ADR-054 (auto-select) and ADR-055 (on-demand tailoring). |
| `notebooks/phase_7_validation.ipynb` | Path detection robust to launch dir. Cell 20 (mock-mode regression) seeds `mock_deps.resume_repo.get_by_id` with a valid profile. Real-mode cells (gated by `REAL_MODE = bool(ANTHROPIC_API_KEY)` from `.env`) unchanged — still require an API key. |

Verified: all phases 1-6 execute end-to-end with no `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` set; phase 7 also passes in mock mode (`.env` temporarily moved aside) and is structurally ready for live mode when keys are available.

### Fixed — Workflow Detail showed the most recent run regardless of which history row was clicked

- `app/ui/streamlit_app.py` — clicking a row in **Workflow History** now actually loads that run's detail. Root cause: the Detail view's `st.text_input("Workflow ID", value=...)` had no `key`, so Streamlit retained the widget's previous value across reruns and ignored the freshly-set `detail_workflow_id`, clobbering it back to whatever was last in the input. Replaced with a keyed input + `_detail_wf_synced` sentinel; `_navigate()` clears the sentinel whenever it sets a new `detail_workflow_id` so the widget re-syncs on the next render. User-typed values are still preserved across unrelated reruns.
- Same file, **Workflow History** table — long columns shrunk to keep the table scannable: `Run` shows the first role + first location with `+N` badges (was 2 of each, `width="large"`), `ID` shows the first 8 chars + ellipsis, `Stage` and `Progress` dropped from `medium` to `small`. Row-click handler now reads the workflow_id from the source `df` (not `display_df`) so the truncation doesn't break navigation.

### Changed — LLMClient typed-usage migration (5 stacked PRs)

Surfaced by `/api-and-interface-design` applied to `app/providers/llm_client.py`. The legacy `last_call_usage() -> tuple[int, int, float]` side-channel was racy under concurrency (mitigated by thread-locals at every layer, but still fragile) and the positional-tuple shape blocked future-additive fields like latency. Replaced with a typed return-value usage object, migrated as 5 small independently-revertable PRs.

| PR | Commit | Scope |
|----|--------|-------|
| 1/5 | `984cd9f` | Add `LLMUsage` frozen dataclass + `LLMClient.complete_with_usage(...)` ABC method with a default impl that calls `complete()` then `last_call_usage()`. Pure addition; 5 new tests. |
| 2/5 | `0a37380` | `BaseAgent._run()` calls `complete_with_usage()` internally and stores the typed usage in thread-local. New `BaseAgent.last_call_usage_typed() -> LLMUsage` accessor. Legacy tuple accessor preserved for back-compat. |
| 3/5 | `b11b1f0` | `score_jobs._score_one()` reads typed usage via new `safe_agent_usage_typed()` helper. Same numbers reach `add_llm_calls_bulk`. |
| 4/5 | `5979672` | Remaining 4 nodes migrated: `career_advice`, `interview_prep`, in-graph `tailoring`, parallel `deep_review`. After this PR no production caller uses the legacy tuple helper. |
| 5/5 | (this) | Remove the now-unused `safe_agent_usage()` tuple helper from `app/workflows/limits.py`. Provider-layer `last_call_usage()` is kept as a documented-deprecated implementation detail (removing it would require touching every provider test for marginal value). |

Net result: every LLM call site reads usage as a typed `LLMUsage` instead of a positional tuple. Adding a future field (e.g. `latency_ms`) is now a one-line additive change instead of a breaking signature update.

### Changed — Tailoring REST surface aligned with project conventions

Surfaced by the `/api-and-interface-design` skill applied to `app/api/routers/`. The on-demand tailoring router (ADR-055) had drifted from the conventions established by the existing routers — verb-vs-noun and singular-vs-plural inconsistencies that would compound as more endpoints were added. Fixed in one PR:

- `POST /workflows/{wf}/jobs/{job}/tailor` → `POST /workflows/{wf}/jobs/{job}/tailorings` (plural noun, no verb in URL).
- `POST /tailorings/{id}/decision` → `POST /tailorings/{id}/decisions` (plural, matches existing `/workflows/{id}/decisions`).
- `GET /tailorings/{id}` and `POST /tailorings/{id}/decisions` deliberately kept top-level (the `tailoring_id` is a globally unique UUID; same pattern as GitHub's `/repos/.../issues` for list vs `/issues/{id}` for fetch). Documented in `app/api/routers/tailoring.py` header and `api_reference.md`.

### Added — Typed `TailoringResponse` schema and global validation-error handler

- New `TailoringResponse` and `TailoringListResponse` Pydantic models in `app/api/schemas/responses.py`. The tailoring router endpoints now declare `response_model=TailoringResponse` so the response shape is enforced at the API boundary. Consumer-side typing of `api_client.py` is intentionally deferred to a follow-up so the UI doesn't need refactoring in the same change.
- New global `RequestValidationError` handler in `app/api/main.py` normalises Pydantic 422 responses to the same `{detail: {error, message, details}}` shape that hand-raised `HTTPException`s already use across the rest of the API. Without this, Pydantic's default 422 surfaced a top-level error list that broke the consumer's ability to read errors uniformly. New regression assertion in `tests/v2/test_tailoring_router.py::test_decision_invalid_value_422` locks in the normalised shape.

### Performance — deep_review now processes selected jobs concurrently

- `app/workflows/nodes/deep_review.py` refactored from a sequential `for job in selected_jobs:` loop to a `ThreadPoolExecutor(max_workers=5)` fan-out, mirroring the ADR-049 template that score_jobs uses.
- Estimated ~4× wall-clock speedup at `MAX_SELECTED_JOBS = 10` (ADR-054). Critical for daily use since deep review is now the dominant cost in a full run.
- Pre-flight budget cap: `(MAX_LLM_CALLS_PER_RUN - calls_used) // (MAX_REVIEW_ROUNDS * 2)` jobs reviewed; the rest are budget-skipped (matches score_jobs's safety pattern).
- Final-review selection deterministic: walk `selected_jobs` in input order, pick the last `best_review` — preserves the previous "last writer wins" semantics in spite of nondeterministic worker completion order.
- New regression test `test_deep_review_runs_jobs_concurrently` in `tests/v2/test_workflow_nodes.py` locks in concurrency: 5 jobs × 100ms agent calls must complete in <300ms (would be 500ms+ sequential). 450 tests pass total.
- Surfaced by the `/performance-optimization` skill applied to the file.

---

## 2026-05-02

### Fixed — Tailoring drafts rejected when Claude omits narrative-summary fields

The first real on-demand tailoring run failed with a Pydantic ValidationError: Claude returned a draft with usable per-bullet suggestions but omitted `skills_section_suggestions`, `overall_tailoring_notes`, and `fidelity_risk_summary`. The schema marked all three as required, so the entire response was rejected (and the schema-repair pass that followed pushed the request past the HTTP timeout).

- `app/schemas/tailored_resume_draft.py` — those three narrative-summary fields are now tolerant: empty list / empty string defaults. The load-bearing per-bullet fields (`supporting_evidence`, `claim_type`, `fidelity_risk`) remain required, so the fidelity invariants are unchanged.
- New regression test `test_tailored_resume_draft_summary_fields_optional` in `tests/v2/test_schemas.py` constructs a draft omitting all three to lock the behaviour in.

### Added — On-demand resume tailoring is now wired up end-to-end

The TailoringAgent and FidelityReviewer have existed since Phase 4 with full evidence-binding semantics (every claim cites the original resume; missing experience labelled as a gap), but they were UI-dark — `state["user_requested_tailoring"]` defaulted to `False` and nothing ever flipped it. This change adds an out-of-graph trigger so any deep-reviewed job can be tailored on demand.

- `app/workflows/nodes/tailoring.py` — fixed latent bug: `tailoring_repo.create()` was called with 4 args; the repo expects 5. Would have exploded the moment the in-graph path actually ran.
- `app/api/routers/tailoring.py` (new) — four endpoints:
  - `POST /workflows/{wf}/jobs/{job}/tailor` runs TailoringAgent + FidelityReviewer synchronously, persists the draft, returns `{tailoring_id, tailored, fidelity_review, ...}`.
  - `GET /workflows/{wf}/tailorings` lists drafts for a workflow.
  - `GET /tailorings/{id}` fetches one.
  - `POST /tailorings/{id}/decision` accepts `{approval: approve|revise|reject}`.
- `app/repositories/tailoring_repository.py` — added `set_decision()`, `list_by_workflow()`, `get_by_id()`. Migration adds `fidelity_review_json`, `decision`, `decided_at` columns to `tailored_resumes` (try/except ALTER TABLE pattern, safe for existing DBs).
- `app/api/dependencies.py` — exposed `get_deps()` so routers can inject individual agents/repos without rebuilding the graph.
- `app/ui/streamlit_app.py` — Workflow Detail now has a "Resume Tailoring" section: per-job expander with **✨ Generate new draft**, side-by-side `original → suggested` diffs (with the cited resume evidence under each suggestion), claim-type and fidelity-risk badges, fidelity flag panel, and **Approve / Request revision / Reject** buttons.
- 9 new tests in `tests/v2/test_tailoring_router.py` covering happy path, evidence-presence invariant, gap-label invariant, missing workflow / resume_profile, decision validation.

The existing in-graph `await_tailoring_approval` interrupt is unchanged; it remains as the path for users who want to set `user_requested_tailoring=True` before a run starts (still no UI surface for that case).

### Changed — Deep review now scales to all qualifying jobs (ADR-054)

- `MAX_SELECTED_JOBS` raised from 3 → 10 in `app/workflows/limits.py` so every job whose best track score meets `effective_config.scoring.min_match_score` advances to the deep review chain. Previously only the top 3 qualifying jobs were processed and the rest were silently truncated (visible only via the `selected_jobs_cap` finding in the Workflow Detail Limits panel).
- `MAX_LLM_CALLS_PER_RUN` raised from 100 → 200 to absorb the worst-case ~60–80 deep-review LLM calls when all 10 discovered jobs qualify, plus scoring/research overhead.
- `app/api/schemas/requests.py` — `JobSelectionDecision.selected_job_ids` `max_length` now bound to the `MAX_SELECTED_JOBS` constant instead of the literal `3`.
- `app/ui/streamlit_app.py` — Live Run Monitor LLM-calls metric now derives the denominator from `MAX_LLM_CALLS_PER_RUN` instead of a hardcoded `100`.
- `config/config.example.yaml` — `limits.max_selected_jobs` (3 → 10) and `limits.max_llm_calls_per_run` (100 → 200) updated to match the new enforcement constants.
- ADR-052 status amended to flag that its "10 jobs is sufficient to find 3 strong matches" framing is superseded by ADR-054.

### Fixed — Stale limit constants

- `app/ui/streamlit_app.py` — LLM calls metric display updated from `/ 50` → `/ 100` to match `MAX_LLM_CALLS_PER_RUN = 100` set in Phase 9
- `config/config.example.yaml` — `max_llm_calls_per_run` updated from 50 → 100 to match actual enforcement constant

### Added — v2 User Guide

- `docs/user_guide.md` — rewritten from scratch against the actual implemented v2 UI
  - Accurate sidebar navigation (13 items in 3 groups: Active Run, Browse Results, Analytics)
  - **Start New Run** form: Resume ID text field, comma-separated roles/locations, career track radio — corrects prior version that described a nonexistent file upload
  - **Monitor / HITL** view: status indicators, metrics (LLM calls / 100, cost, errors), job selection checkboxes, tailoring approval (Approve / Request Revision / Reject)
  - **Browse Results**: min score slider, search, per-track score columns, track tables with progress bar scores and URL links
  - **Deep Review Results** and **Interview Prep**: workflow-scoped views showing resume/career gaps, 7-day prep plan, weak areas
  - **Companies** and **Run History** analytics views
  - Session state note: browser session state resets on reload; historical data always available via Browse views

---

### Changed — Phase 9: Cost Optimization

#### Model Tiering (75–85% cost reduction per run)
- `ResearchAgent` moved from Sonnet → Haiku — runs every job; summarization does not require Sonnet-level reasoning
- `ReviewAuditor` moved from Sonnet → Haiku — validation/checking task, not generative
- `FidelityReviewer` moved from Sonnet → Haiku — validation/checking task, not generative
- `ScoringAgent`, `ResumeCritic`, `CareerAdvisor`, `InterviewCoach`, `TailoringAgent` unchanged
- `app/api/dependencies.py` — agent → provider assignment updated with inline rationale comments

#### Volume Cap
- `MAX_JOBS_PER_RUN` reduced from 20 → 10 in `app/workflows/limits.py` — halves research + scoring call volume per run

### Added — Documentation Overhaul

#### `docs/wiki.md` — new wiki landing page
- 20-section wiki covering the full system: architecture, agents, data model, state, workflows, config, observability, security, HITL, patterns, principles, ADRs, phase history, testing, migration, dependencies, changelog
- Each section has a prose summary and explicit links to the authoritative detail file

#### `docs/architecture/implementation_plan.md` — phases 7, 8, 9 added
- **Phase 7** (complete): live agent gate, `SqliteSaver`, real scrapers, `.env` loading, phase validation notebook
- **Phase 8** (complete): concurrent scoring via `ThreadPoolExecutor` (75s → 20s), `ConcurrentAdzunaScraper`, `add_llm_calls_bulk`
- **Phase 9** (in progress): model tiering table, volume cap, cost impact estimates, review gate
- Review gate summary table extended to Gates 7–9
- Execution limits table updated (`MAX_JOBS_PER_RUN = 10`)

#### `README.md` — rewritten for v2
- Updated architecture Mermaid diagram (8 agents, LangGraph, SqliteSaver, FastAPI/Streamlit split)
- Agents table with model assignments and trigger conditions
- Run instructions updated (`uvicorn` + `streamlit`, not `python main.py`)
- Cost section replaced with v2 scenario table and execution limits
- Patterns table updated with LangGraph-era patterns
- Tech stack reflects actual v2 stack

---

## 2026-04-24

### Fixed

#### Dashboard Timestamp Parsing (mixed naive / tz-aware formats)
- `pd.to_datetime(..., utc=True)` coerces naive ISO timestamps (written by `utcnow()` before 2026-04-15) to NaT in this pandas version, while tz-aware `+00:00` strings (written by `now(tz=UTC)` after 2026-04-15) parsed correctly. This caused:
  - Run History: all timestamps for runs 1–14 displayed as blank / None
  - Run History: runs 15–18 were invisible entirely (NaT rows sorted away)
  - Top Matches / track tables: `Found` column blank for older jobs
- Fixed by extracting `_parse_utc(series)` helper in `dashboard.py` that strips the `+00:00` suffix before calling `pd.to_datetime`, so both timestamp formats land as identical naive UTC strings. Applied to `run_at` in `load_runs()` and `found_at` / `posted_at` in `load_jobs()` and `load_new_jobs()`.

---

### Added

#### US State Extraction and Filtering
- `extract_us_state(location)` added to `models/filters.py` — parses a 2-letter US state abbreviation from any unstructured location string. Handles `"Atlanta, GA"`, `"Austin, Texas"`, `"Washington, DC"`, multi-word states (longest-match-first), and returns `None` for `"Remote"` or non-US locations.
- `state: Optional[str]` field added to `Job` model with a `@model_validator(mode="after")` that auto-fills from `location` on construction — all three scrapers get state extraction for free with no code changes.
- `state TEXT` column added to the `jobs` table via `_MIGRATIONS` — populated on insert for new jobs; `backfill_states()` fills existing rows on startup.
- `Database.backfill_states()` — idempotent method that runs on every `main.py` startup to populate `state` for rows where it is `NULL`.
- **Dashboard state filter** — "Filter by state" multiselect in the sidebar. Applies to Top Matches, IC Track, Architect Track, Management Track views, and the scored-jobs table and job cards in the New Jobs view.
- **State column** in all job listing tables (Top Matches, IC/Architect/Management track tables, New Jobs scored, New Jobs unscored).
- **State column** in the Rich terminal table printed after each scoring run.

#### Low-Score Purge
- `Database.delete_below_threshold(threshold, dry_run=False)` — hard-deletes scored jobs where `score_best < threshold`. `status = 'applied'` and `status = 'offer'` rows are always protected. `dry_run=True` returns the count without deleting.
- `--purge` CLI flag — shows a count preview, requires explicit `y` confirmation, then calls `delete_below_threshold`. Default cutoff is 75.
- `--threshold N` CLI flag — override the purge cutoff (e.g. `--threshold 80`).
- `MIN_PERSIST_SCORE = 75` constant in `agents/scoring_agent.py` — jobs scored below 75 on all active tracks are deleted immediately after scoring and never reach `status=SCORED`. Eliminates the need for periodic cleanup of newly scraped jobs.
- `Database.delete_job(job_id)` — single-row hard delete by primary key, used by the scoring agent for immediate discard.

#### State Inference from County and City Names
- `_COUNTY_STATE` dict added to `models/filters.py` — maps ~100 unambiguous county/parish/borough base names to their state (e.g. `"fulton"→"GA"`, `"king"→"WA"`, `"harris"→"TX"`, `"hudson"→"NJ"`, `"hartford"→"CT"`). Covers all major US metro counties.
- `_CITY_STATE` dict added to `models/filters.py` — maps ~200 major US cities and NYC boroughs to their state (e.g. `"manhattan"→"NY"`, `"san francisco"→"CA"`, `"the woodlands"→"TX"`).
- `extract_us_state()` extended with two new fallback steps:
  - **Step 4** — regex-matches `"[Name] County/Parish/Borough"` substrings and looks up the base name in `_COUNTY_STATE`. Handles `"Atlanta, Fulton County"`, `"Seattle, King County"`, `"Jersey City, Hudson County"`, etc.
  - **Step 5** — splits the location on commas and checks each segment against `_CITY_STATE`. Handles `"Grand Central, Manhattan"`, `"Nob Hill, San Francisco"`, etc.
- Live database backfilled: NULL state rows reduced from ~800 to 63 (40 are bare `"US"` entries with no resolvable state; 23 genuinely uncoverable).

### Updated
- `docs/architecture.md` — updated dashboard data-flow diagram (state filter in sidebar), main-run flow (backfill step), component diagram (`extract_us_state` link), and mindmap (two new patterns: Location Normalisation, Focused Pipeline Management).
- `docs/features.md` — new CLI commands table, state filter in sidebar controls, three new rows in Feature Summary.
- `docs/dashboard.md` — sidebar controls table, data loading section, job cards section.
- `docs/main.md` — commands table, startup sequence, new `cmd_purge` in key functions, Purge command section.
- `docs/storage/db.md` — `state` column in schema, two new write-operations (`backfill_states`, `delete_below_threshold`).
- `docs/models/job.md` — `state` field in Metadata Fields table, `_fill_state` validator section.
- `docs/models/filters.md` — new US State Extraction section with examples and design notes.
- `docs/user_guide.md` — state filter in sidebar controls, new "Pruning low-quality matches" section.
- `CLAUDE.md` — updated Running the Agent command reference.

---

## 2026-04-17

### Fixed
- Update docs to match deprecated-API fixes from 2026-04-15: replace `datetime.utcnow()` references with `datetime.now(tz=timezone.utc)` and old Pydantic `class Config` snippet with `model_config = ConfigDict(...)` in `docs/models/job.md`, `docs/main.md`, `docs/storage/db.md`, `docs/architecture.md`, and `docs/blog_draft_patterns_v2.md`
- Blog draft `BEFORE` code block intentionally preserves `utcnow()` to illustrate the original bug

---

## 2026-04-15

### Fixed
- Replace deprecated `datetime.utcnow()` with `datetime.now(tz=timezone.utc)` across all files — `utcnow()` is deprecated in Python 3.12 and emits `DeprecationWarning` on Python 3.13 (`dashboard.py`, `main.py`, `models/profile.py`, `storage/db.py`, `tests/test_adzuna_scraper.py`, `tests/test_db.py`)
- Replace deprecated Pydantic v2 inner `class Config` with `model_config = ConfigDict(...)` in `models/job.py` and `models/profile.py`
