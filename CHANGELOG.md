# Changelog

All notable changes are documented here, grouped by date.

---

## 2026-05-26

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
