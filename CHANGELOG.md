# Changelog

All notable changes are documented here, grouped by date.

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
