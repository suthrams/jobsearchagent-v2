# Changelog

All notable changes are documented here, grouped by date.

---

## 2026-05-05

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
