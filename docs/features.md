# Job Search Agent v2 — Features & Capabilities

A multi-agent career intelligence system: discovers jobs, scores fit across the career tracks each profile pursues (ADR-071), reviews high-match roles in depth, prepares you for interviews on demand, and tailors your resume — all via a FastAPI backend and Streamlit UI, orchestrated with LangGraph and powered by Claude (with an optional OpenAI provider).

> Counts in this doc (table totals, test totals) are stated as of the last edit and drift over time — the ADR index and CI are the live source of truth.

---

## Table of Contents

1. [Job Discovery](#1-job-discovery)
2. [Pre-Scoring Filters](#2-pre-scoring-filters)
3. [Company Research](#3-company-research)
4. [Job Scoring — Career Tracks](#4-job-scoring--career-tracks-per-profile-adr-071)
5. [Deep Review — Critic + Auditor Loop](#5-deep-review--critic--auditor-loop)
6. [Career Advice](#6-career-advice)
7. [Interview Preparation](#7-interview-preparation)
8. [Resume Tailoring + Fidelity Review](#8-resume-tailoring--fidelity-review)
9. [Resume Clinic](#9-resume-clinic)
10. [Human Decision Points (out-of-graph)](#10-human-decision-points-out-of-graph)
11. [Run Lifecycle Controls](#11-run-lifecycle-controls)
12. [Observability & Cost Tracking](#12-observability--cost-tracking)
13. [Security & Privacy](#13-security--privacy)
14. [Workflow Checkpointing](#14-workflow-checkpointing)
15. [Configuration](#15-configuration)
16. [Multi-User Profiles](#16-multi-user-profiles-adr-062)
17. [Backend API](#17-backend-api)
18. [Feature Summary](#18-feature-summary)

---

## 1. Job Discovery

Jobs are discovered from multiple sources concurrently on every run. Discovery honors the run's `search_criteria` (ADR-064): when the run supplies `roles`, a per-run Adzuna scraper is built from them; with no roles the built-in scraper uses the configured titles. Locations are taken one-per-line (so "City, State" is never comma-split), and a "Remote" location triggers the remote search.

### Adzuna (automated, concurrent)
- Searches a list of job titles across multiple cities simultaneously via `ConcurrentAdzunaScraper` (5 workers)
- Separate keyword list for US-wide remote searches
- Configurable search radius in kilometres
- Free-tier quota guard: `(titles x locations) + remote_keywords` is kept under 100/day by the config commentary

### ATS-direct — Greenhouse + Lever (opt-in, ADR-081)
- Source-of-truth employer feeds: listings are live by definition and the apply URL is the employer's own ATS page (no dead-link / 429 problem that Adzuna can have)
- Queried **per company** — list the board tokens/slugs of target companies under `scrapers.greenhouse.companies` / `scrapers.lever.companies` (empty list = off)
- Built per run by `WorkflowDependencies.ats_scraper_factory`; additive alongside Adzuna

### LinkedIn + custom URLs (manual intake)
- LinkedIn blocks automated scraping — the built-in `LinkedInScraper` reads job URLs from `data/linkedin_inbox.txt` (one per line) and clears entries it has processed
- Arbitrary job-posting URLs entered in the Start New Run form flow through `CustomUrlScraper` (`state["custom_urls"]`): extraction tries heuristics first (JSON-LD -> OpenGraph -> article), falls back to an LLM extractor (`custom_url_extractor`), then logs-and-skips on failure. 25-URL hard cap, 30s/URL timeout

### Deduplication & timeouts
- Jobs are deduplicated by URL and by title + company (case-insensitive) across all sources
- `JobDiscoveryService.discover()` enforces a 180s per-scraper timeout so one slow source cannot stall the run

### Funnel width (configurable, ADR-060/061)
- **Discovered:** plain auto mode discovers only as many as it will score; wide-net modes (manual selection or the relevance pre-filter) widen up to `MAX_DISCOVERED_JOBS = 50` via `search.max_discovered`. Read through `get_max_discovered_jobs(state)`
- **Scored:** `MAX_JOBS_PER_RUN = 10` default, overridable per run via `scoring.max_scored`, clamped to `MAX_SCORED_CEILING = 25`. Read through `get_max_scored(state)`

---

## 2. Pre-Scoring Filters

Several filter layers remove noise before (and one cheaply during) the expensive scoring pass.

### Deterministic keyword gates (no LLM)
Applied at scrape time and again before research/scoring, sharing one module (`models/filters.py`) so the keyword lists stay in sync:
- **Title relevance gate** — title must contain a keyword from `RELEVANT_TITLE_KEYWORDS`
- **Title exclusion gate** — titles in `EXCLUDED_TITLE_KEYWORDS` are dropped (property manager, sales engineer, intern, etc.)
- **Tech description gate** — description must contain at least one keyword from `TECH_DESCRIPTION_KEYWORDS`

### Posting-age staleness (opt-in, ADR-080)
- `search.max_posting_age_days` drops postings older than N days at discovery (deterministic, no network fetch). Stale postings correlate with pulled requisitions / dead apply links. Postings with no parseable date are kept; `0`/absent = off. `posted_at` is persisted and surfaced on Job Detail

### Experience targeting (opt-in, ADR-065)
- A `[min, max]` years window via `search.min_years_experience` / `search.max_years_experience` (`0`/None = that bound off), plus `search.exclude_senior` to drop senior/principal/staff/lead/director roles. Deterministic (`app/services/experience_filter.py`); off by default

### Relevance pre-filter (opt-in, LLM, ADR-079)
- When `search.relevance_filter` is on (and manual selection is off), the in-graph `relevance_filter` node makes **one cheap batched Haiku call** that hard-drops clear seniority/relevance mismatches before scoring
- Profile-relative and bidirectional — verdict `mismatch in {none, too_senior, too_junior, unrelated}`, judged against the profile's own band
- **Never loses a run:** any agent failure / unparseable / empty verdict keeps ALL jobs (logged to `errors[]` + `discovery_stats`). The profile enters the agent only via `trim_resume_profile()` (PII seam)

---

## 3. Company Research

Every job passes through the Research Agent before scoring.

- Pattern: **Bounded ReAct** — up to `MAX_RESEARCH_STEPS = 2` tool steps
- Tools: job content fetcher, description extractor
- Output: `ResearchContext` — company summary, role context, technology/leadership/domain signals, risk flags, confidence score
- Model: **Haiku** (high-volume: runs for every job)

Research context is injected into the Scoring Agent prompt, improving accuracy for roles where the job description alone is sparse.

---

## 4. Job Scoring — Career Tracks (per-profile, ADR-071)

Each job is scored against your resume independently on the career tracks the profile pursues. The three tracks are fixed; a profile picks the subset that applies (default all three).

| Track | Score field | Target Roles |
|---|---|---|
| `ic` | `technical_score` | Senior / Staff / Principal Engineer |
| `architect` | `architecture_score` | Solutions / Principal / Enterprise Architect |
| `management` | `leadership_score` | Senior Manager / Director / Head of Engineering / VP |

### How scoring works
- All scored jobs run **concurrently** via `ThreadPoolExecutor` (5 workers)
- Each job receives an overall score (0–100), a `domain_score`, plus a sub-score for each **active** track. Inactive tracks are scored `null` — not evaluated at all (ADR-071), so they cannot be confused with a genuine low score
- Output includes: match summary, strengths, gaps, recommended next action, confidence
- Model: **Haiku** (cheapest model; concurrent execution keeps latency low)
- Active tracks come from `effective_config.scoring.tracks` (set per profile in **Settings → Scoring**). Absent/empty = all three. `scoring.career_track` is a separate weighting-emphasis hint, not an inclusion list
- The resume entering the scorer is narrowed by `project_resume_for_scoring()` (ADR-086) on top of the PII-redaction seam

### Score-driven routing
After scoring, every job whose best **active** track score meets `effective_config.scoring.min_match_score` (default 75) auto-advances to deep review (a job that clears the threshold only on an inactive track does **not** qualify). Use `qualifies_for_deep_review()` / `best_track_score()` with the run's `active_track_keys(state)`. `await_job_selection` auto-selects up to `MAX_SELECTED_JOBS = 3` qualifying jobs (highest best-track score wins) — there is no in-graph job-selection pause.

---

## 5. Deep Review — Critic + Auditor Loop

For each selected job, a reflection loop produces a thorough resume review.

### Resume Critic
- Pattern: **Critique**
- Input: job posting + resume profile + research context + job score
- Output: `ResumeReview` — section-by-section analysis, critical gaps (resume gaps vs career gaps distinguished), suggested improvements, questions for the user
- Model: **Haiku** (cost-tuned; the auditor polices its quality)

### Review Auditor
- Pattern: **Evaluator / Reflection**
- Input: critic's review + job posting
- Output: `ReviewAudit` — audit score (0–100), quality summary, missing analysis points, generic-feedback flags, unsupported claims
- Model: **Haiku** (validation/checking task)

### Reflection loop
- Runs until: `audit_score >= AUDIT_QUALITY_THRESHOLD (75)` OR stagnation (< 5-point improvement) OR `MAX_REVIEW_ROUNDS = 2`
- The best review across all rounds is persisted (per-round detail in `review_rounds`)

---

## 6. Career Advice

After the deep review loop, the Career Advisor synthesizes findings across all selected jobs.

- Pattern: **Advisory**
- Input: all job scores + all reviews + resume profile
- Output: `CareerAdvice` — track recommendations, positioning strategy, skill-gap priorities, suggested timeline
- Model: **Sonnet** (generative advisory prose)
- Runs once per workflow run, not per job

---

## 7. Interview Preparation

On-demand interview coaching (ADR-085 — cost control).

- **On-demand by default:** trigger via `POST /workflows/{wf}/jobs/{job}/interview-prep` for any scored job
- The in-graph coach auto-fires only when `scoring.auto_interview_prep` is on (default off) or `user_requested_interview_prep` is set — read via `get_auto_interview_prep(state)`. (Because the top selected job always clears `min_match_score`, auto-firing meant the Sonnet coach ran nearly every run.)
- Output: `InterviewPrep` — likely questions, suggested answers, topics to research, red flags to address
- Model: **Sonnet** (generative coaching content)

---

## 8. Resume Tailoring + Fidelity Review

Evidence-bound resume tailoring with a mandatory fidelity guardrail, run out-of-graph on demand for any scored job (`POST /workflows/{wf}/jobs/{job}/tailorings`, ADR-055).

### Tailoring Agent
- Pattern: **Evidence-bound generation**
- Every tailored claim must include `supporting_evidence` referencing the original resume; missing experience is labeled a gap, never rewritten as if present (ADR-015/059)
- Honors a page budget + section grouping + headline/impact controls (ADR-056) and per-job exclusion (ADR-057)
- Output: `TailoredResumeDraft` — section rewrites with evidence citations, identified gaps
- Model: **Sonnet** (quality-critical; the user acts on this output)

### Fidelity Reviewer
- Pattern: **Validation / Guardrail**
- Runs automatically after every Tailoring Agent call — cannot be bypassed (the on-demand router enforces it)
- Flags any claim unsupported by the original resume
- Output: `FidelityReview` — pass/fail per claim, flagged fabrications, overall verdict
- Model: **Haiku** (binary verification task)

A human `edit` decision is the owner's own final draft — trusted as-is and **not** re-reviewed (ADR-059: the reviewer polices the agent, not the human). Decisions are recorded via `POST /tailorings/{id}/decisions` (`approve` / `revise` / `reject` / `edit`).

### Tailoring live chat + export (ADR-072)
A scored job's tailored draft can open a chat session (`POST /tailorings/{tid}/chat-session`) that reuses the Resume Clinic chat + export stack, seeded deterministically from the draft.

---

## 9. Resume Clinic

A standalone, job-agnostic resume review surface (ADR-066), out-of-graph like tailoring.

- **Review:** `POST /users/{id}/resume-clinic` runs the `ResumeReviewerAgent` (Sonnet, structured output) + `FidelityReviewer` on its `rewrites`. The runner writes a lightweight `workflow_runs` row (`workflow_type="resume_clinic"`) purely as the cost-attribution correlation id
- **Chat-revise loop (ADR-068):** `POST /resume-clinic/{id}/chat` runs the `ResumeChatAgent` (Sonnet) one call per turn, each followed by Fidelity on any rewrites; bounded to `MAX_CHAT_TURNS_PER_CLINIC = 25` turns with a session-cost meter
- **Decisions:** `approve` / `revise` / `reject` / `edit` (a human `edit` is not re-reviewed)
- **Export:** deterministic, decision-aware renderer (`compose_resume`) — no LLM — emits md / txt / html / json / docx / pdf. Placeholders survive verbatim; unmatched rewrites are appended, never dropped
- `RoleDataProvider` is a pluggable seam (v1: `NullRoleDataProvider`); its `lookup` never raises — graceful fallback to LLM-only is the contract

---

## 10. Human Decision Points (out-of-graph)

The workflow runs end to end with **no in-graph pause** — ADR-059 retired the `interrupt()` / `waiting_for_user` path. Job selection auto-selects qualifying jobs; human judgment enters through out-of-graph, on-demand operations the user triggers from the UI after (or alongside) a run, plus one optional curate-before-scoring phase. The backend always validates a decision before persisting it; the UI never auto-approves agent output.

| Decision point | How it works |
|---|---|
| Manual scoring selection (opt-in, ADR-060) | When enabled, discovery casts a wide net and the run parks at `awaiting_scoring_selection`; the user picks which jobs to score, then a second phase scores only those (no `interrupt()` — the choice sits between two phases re-entering the same thread) |
| Deep review / interview prep on demand (ADR-061/085) | The user triggers a single-job critic+auditor loop or interview coaching for any scored job |
| Tailoring decision (ADR-055/059) | The user runs tailoring on a scored job, then records approve / revise / reject / edit. An `edit` is the human's own final draft, trusted as-is and not re-reviewed |
| Resume Clinic decision (ADR-066) | The user reviews job-agnostic resume rewrites and records approve / revise / reject / edit |
| Run cancellation (ADR-083) | The user can request cooperative cancellation of a running workflow; it stops at the next node boundary |

There is no Apply / Save / application-status feature by design — the career decision point stays human-owned (see the "No application tracking" rule in `CLAUDE.md`).

---

## 11. Run Lifecycle Controls

Guards around kickoff, re-submit, and cancellation.

- **Idempotent kickoff (ADR-082):** `POST /workflows` accepts an optional `Idempotency-Key` header — same key + same body replays the original `202`; same key + different body returns `409 idempotency_key_reused`. The atomic claim is `IdempotencyRepository.claim()`. Absent the header, behaviour is unchanged
- **Concurrent re-submit guard:** `POST /workflows/{id}/retry` and `.../scoring` are protected by the process-local registry in `app/workflows/run_control.py` (`try_acquire_running`) -> `409 workflow_already_running` (process-local; a multi-worker rollout would need a shared lock)
- **Cooperative cancellation (ADR-083):** `POST /workflows/{id}/cancel` sets a flag that `_instrument_step` checks at each node boundary, raising `WorkflowCancelled`. Granularity is one node; statuses `cancelling` / `cancelled`; `409 workflow_not_cancellable` when nothing is pending

---

## 12. Observability & Cost Tracking

Every meaningful event is recorded; ADR-073/074 wired the previously-dormant tables and ADR-075 routes all UI reads through the API.

| Wired table | What is recorded |
|---|---|
| `workflow_runs` | Run start/complete/fail, status transitions, final metrics |
| `step_executions` | Per-node start/complete/fail (`_instrument_step`) — surfaced as "slowest steps" |
| `agent_events` | Per-agent events incl. `schema_repaired` (ADR-078) and `custom_url_extractor` |
| `llm_calls` | Token counts, cost, latency, model, prompt version — incl. billed-but-failed calls (ADR-077) |
| `human_decisions` | Decision type + flags for every out-of-graph artifact decision (PII-safe) |
| `api_requests` | Every REST request by matched route template (never the raw path/query) |
| `security_events` | Guardrail trips (see [Security & Privacy](#13-security--privacy)) |
| `run_metrics` | Per-run rollup (finalized or lazily derived via `run_metrics_rollup`) |

### Cost visibility
- `estimated_cost_usd` accumulates per LLM call and per run; failed-but-billed completions are attributed (ADR-077)
- The **Cost Dashboard** shows per-agent / per-model breakdowns plus week-by-week and by-model charts; the **System Dashboard** shows reliability, security, decisions, slowest steps, schema-repair rate (a Tier-1 drift proxy, ADR-078), and budget-cap trips (ADR-076)
- Dashboards default to the active profile with a system-wide override

### Prompt caching & spend control
- System messages use `cache_control: ephemeral` — large cost reduction on repeated agent calls within the 5-minute window
- Hard cost caps on the high-volume agents and a runtime budget cap that emits `budget_cap_reached` when tripped (ADR-076)

### Health & readiness (ADR-084)
- `GET /health` (liveness) and `GET /readyz` (readiness) are the only unauthenticated routes and are excluded from `api_requests`. `/readyz` probes shared dependencies (database critical -> 503; agent/Adzuna capabilities -> degraded/200) and is secret-safe (presence/mode only). Surfaced on the System Dashboard health tile

---

## 13. Security & Privacy

- **PII redaction at the LLM seam (ADR-069):** every resume profile entering an agent context goes through `redact_pii_for_llm()` / `trim_resume_profile()`, dropping `raw_text` + direct identifiers. The only sanctioned raw-text paths are the resume parser and the clinic Fidelity Reviewer. Enforced by an invariant source-scan test
- **Redacted at rest (ADR-070):** `load_resume` stores the redacted profile in `workflow_runs.state_json` / checkpoints; the un-redacted profile's only at-rest home is the `resumes` row
- **Untrusted job descriptions (ADR-019):** scraped descriptions are never followed as instructions; `app/prompts/shared/guardrails.txt` is auto-injected into every agent prompt
- **Security events (ADR-073):** five deterministic emit sites — `blocked_url_fetch` (high), `pii_redacted` (info), `unsupported_claim` (warning), `cost_cap_violation` (warning), `budget_cap_reached` (warning). Descriptions are PII-safe by construction (counts, field names, reason classes, hostnames only). Emitted only via `ObservabilityService` (never-crash)
- **Explicit-trigger retention (ADR-070):** `purge_old_data()` never runs automatically — fire via `POST /admin/purge`, `tools/purge_data.py`, or the Settings control. Purging a run cascades to all child rows; resumes purge on a separate window
- **Cooperative isolation, not auth:** the multi-user selector decides *which* profile's data a request touches but does not *prevent* naming another id — acceptable for a trusted personal/family tool (see `architecture/security.model.md` §4.1)

---

## 14. Workflow Checkpointing

LangGraph `SqliteSaver` checkpoints workflow state after every node execution.

- Interrupted runs survive backend restarts and can be resumed from the last completed node
- Checkpoint data is stored in `data/v2.db` alongside the application tables; the `checkpoints` table is for resumption only — UI/history reads go through `workflow_runs`
- In mock mode (no `ANTHROPIC_API_KEY`), `MemorySaver` is used instead — suitable for development and testing

---

## 15. Configuration

### config/config.yaml — static defaults (copy from `config.example.yaml`)

```yaml
search:
  titles: [Senior Software Engineer, Staff Engineer, Principal Engineer, ...]
  locations: [Atlanta GA, Remote]
  # opt-in: relevance_filter, max_posting_age_days, min/max_years_experience, exclude_senior

scoring:
  tracks: [ic, architect, management]   # ADR-071 active subset (default all three)
  # opt-in: min_match_score, max_scored, manual_selection, auto_interview_prep, career_track

scrapers:
  adzuna: {enabled: true, country: us, radius_km: 80, results_per_page: 25, ...}
  greenhouse: {enabled: true, companies: []}   # ADR-081, per-company opt-in
  lever:      {enabled: true, companies: []}

retention: {workflow_runs_days: 90, observability_days: 30, security_events_days: 180, ...}

agents:        # ADR-053/058: per-agent (provider, model)
  research_agent: {provider: claude, model: claude-haiku-4-5-20251001}
  career_advisor: {provider: claude, model: claude-sonnet-4-6}
  # ...

models:        # ADR-058: catalog + pricing (edit to learn new models/prices, no code release)
  providers: {claude: [...], openai: [...]}
```

### User-configurable at runtime (per profile, via UI → DB)
Search titles/locations/filters, scoring tracks + thresholds + funnel width, Adzuna/ATS settings, and **per-agent provider + model** (ADR-053). Overrides are stored per `user_id` in `user_config` and merged over the shared YAML defaults; a new profile starts on pure defaults. Restart-to-apply for model changes.

### Locked (policy, not configuration)
Execution limits (`MAX_JOBS_PER_RUN`, `MAX_REVIEW_ROUNDS`, `MAX_LLM_CALLS_PER_RUN`, etc.), safety thresholds, cost caps, and the `HIGH_VOLUME_SAFE_MODELS` allowlist that bounds which models the cost-capped agents (`research_agent`, `scoring_agent`) may use. These are enforced in code and shared by every profile. A per-agent model choice is otherwise free within the registered catalog.

---

## 16. Multi-User Profiles (ADR-062)

One installation can serve several job-seekers (e.g. you and a family member). Each **profile** has its own resume, search defaults, config and per-agent model overrides, learned memory, cost view, and run history. You pick a profile in the sidebar and run as that person; you switch between runs. There is **no login**.

### Design bet

Build the simplest front door that does not foreclose a stronger one later. The expensive, hard-to-reverse work (the data model + a single identity-resolution point) is done once and is identical regardless of the eventual auth model; the cheap, swappable work (the no-auth selector) is isolated to one function, so adding real authentication later is additive, not a rewrite.

Two constraints shape it:

- **Sequential use** — one run at a time; switch profiles between runs. The global singletons (compiled graph, dependencies, agent registry) stay as-is and are rebuilt on profile switch / run kickoff rather than partitioned per user.
- **Cooperative isolation, not a security boundary** — the selector decides *which* profile's data a request reads and writes; with no authentication it does not *prevent* naming another profile's id. Acceptable for a trusted personal/family tool, and stated plainly so it is never mistaken for access control. See `architecture/security.model.md` §4.1.

### Identity anchor — the `users` table

`id` (INTEGER PK; `0` = all pre-existing data, new profiles auto-increment from `1`), `name`, an optional human-only `note`, `created_at`. Deliberately minimal — everything a profile *uses* lives in its own table keyed by `user_id`.

### Per-profile scoping

- **Resumes** — each profile has its own active resume; creating one deactivates only that profile's prior resumes. The Start New Run resume box becomes a picker over the active profile's resumes.
- **Memory** — `memory_items` is isolated per profile (note: long-term memory is designed but not yet wired into the runtime).
- **History / analytics / cost** — read only the active profile's data, with a system-wide toggle on the dashboards.
- **Config** — two layers (`config.yaml` defaults -> per-profile `user_config` overrides). A new profile starts on pure defaults; protected keys and cost caps stay shared.
- Reference columns store the decimal-string form (`"0"`, `"1"`, ...); per-run tables inherit ownership through `workflow_run_id`; `jobs` stays a shared pool.

### One identity seam

A `?user_id=` query parameter (no HTTP headers) resolved by a single backend dependency, `get_current_user_id` (default `"0"`, validated against `users`), mirrored on the UI client. No router parses identity itself, and adding real authentication later changes only that one function.

### Onboarding

The sidebar **Profile** selector switches the active profile; **Add profile** opens a 3-step wizard — identity (name + optional note) -> resume upload (scoped to the new profile) -> default roles/locations (saved as that profile's config). Only step 1 is required.

### Migration and compatibility

Additive and idempotent: a timestamped DB backup is taken, the `users` table and `user_id` columns/indexes are created, profile `0` is seeded, and all pre-existing resumes, memory, runs, and config overrides are backfilled to `"0"`. Fully backward compatible — a request with no `user_id` resolves to profile `0`.

---

## 17. Backend API

The FastAPI backend exposes REST endpoints for all workflow operations; the Streamlit UI is a pure client of them (ADR-075).

```bash
uvicorn app.api.main:app --reload   # starts at http://localhost:8000
# Swagger UI: http://localhost:8000/docs
```

Every endpoint (except the health probes) accepts an optional `?user_id=` (default `"0"`, ADR-062). Endpoint groups (see `architecture/api_surface_overview.md` for the full surface):

- **Profiles:** `GET`/`POST /users`, `PUT /users/{id}`, `POST /users/{id}/resume`, `DELETE /users/{id}/resume/{rid}`
- **Workflows (lifecycle):** `POST /workflows`, `GET /workflows/{id}`, `POST /workflows/{id}/{retry,scoring,cancel}`
- **Workflow reads:** `GET /workflows/{id}/jobs`, `GET /workflows/{id}/report`
- **Per-job on demand:** `POST /workflows/{wf}/jobs/{job}/{tailorings,deep-review,interview-prep}`
- **Tailoring drafts:** `GET /workflows/{wf}/tailorings`, `GET /tailorings/{id}`, `POST /tailorings/{id}/decisions`
- **Resume Clinic:** `POST`/`GET /users/{id}/resume-clinic`, `POST /resume-clinic/{id}/{decisions,chat,discard-edits}`, `GET /resume-clinic/{id}/export`
- **Job exclusion:** `GET /jobs/excluded`, `POST`/`DELETE /jobs/{id}/exclude`
- **Config:** `GET`/`PUT /config`, `GET /config/providers`, `POST /config/reload`
- **Ops / health:** `GET /health`, `GET /readyz` (unauthenticated, ADR-084); `POST /admin/purge` (ADR-070)

---

## 18. Feature Summary

| Capability | Status |
|---|---|
| Multi-source discovery (Adzuna, LinkedIn, custom URLs, ATS-direct Greenhouse/Lever) | ✅ |
| Concurrent Adzuna scraping (5 workers) + per-run search criteria | ✅ |
| Deterministic keyword gates (title + description) | ✅ |
| Posting-age + experience-targeting filters (opt-in) | ✅ |
| Relevance pre-filter — one cheap Haiku call, keep-all on failure (opt-in) | ✅ |
| Company research — bounded ReAct agent | ✅ |
| Concurrent scoring — per-profile active career tracks (5 workers) | ✅ |
| Deep review — critic + auditor reflection loop (≤ 2 rounds) | ✅ |
| Resume gap vs career gap distinction | ✅ |
| Career advice — cross-job positioning synthesis | ✅ |
| Interview coaching — on-demand by default (ADR-085) | ✅ |
| Evidence-bound resume tailoring + live chat + multi-format export | ✅ |
| Fidelity guardrail — blocks fabricated claims | ✅ |
| Standalone Resume Clinic (review + chat-revise + export) | ✅ |
| Out-of-graph human decisions (no in-graph interrupt, ADR-059) | ✅ |
| Run lifecycle controls — idempotent kickoff, re-submit guard, cooperative cancel | ✅ |
| Observability — 8 wired tables (runs, steps, agents, LLM, decisions, API, security, metrics) | ✅ |
| Per-run/per-call cost tracking + Cost & System dashboards | ✅ |
| PII redaction at the LLM seam + redacted at rest | ✅ |
| Explicit-trigger data retention / purge | ✅ |
| Liveness + readiness health endpoints | ✅ |
| Prompt caching + hard cost caps + runtime budget cap | ✅ |
| Per-agent provider + model selection (Claude + optional OpenAI) | ✅ |
| Hybrid configuration — YAML defaults + per-profile DB overrides | ✅ |
| Multi-user profiles — sequential, no-auth, cooperative isolation (ADR-062) | ✅ |
| FastAPI backend + Streamlit UI (UI reads through API, ADR-075) | ✅ |
| SQLite persistence — 23 application tables | ✅ |
| Test suite — ~937 tests, mock mode, no real API calls in CI | ✅ |
