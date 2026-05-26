# Data Model — jobsearchagent-v2

This document is the authoritative reference for the SQLite schema in `data/v2.db`.
For each of the 19 tables (18 original + `users`, ADR-062) you get the SQL DDL, a per-column data dictionary,
and a "workflow usage" block describing which agent / service / endpoint
writes the rows and which UI helper / endpoint / report reads them.

The single source of truth for the schema is
[`app/repositories/database.py`](../../app/repositories/database.py). If
this document and the SQL diverge, the SQL wins — please open a PR to fix
this document.

---

## 1. Purpose

This document defines:

* the 19 SQLite tables (18 original + `users`, ADR-062), their columns and types
* per-column descriptions (the data dictionary)
* who writes each table and when (workflow usage)
* who reads each table and how (UI / endpoint / report)
* relationships between entities
* how workflow state is persisted
* how observability and security events are tracked
* how memory is structured

The goal is that any contributor can answer "what is column X for, where is
it written, where is it read" without reading code.

---

## 2. Design Philosophy

1. **Workflows are the source of truth.** Every persisted row ties back to
   `workflow_runs.id` (with the exception of `memory_items` which is
   cross-run by design and `user_config` which has no workflow scope).
2. **Store structured outputs, not raw text blobs only.** JSON columns
   carry the structured agent outputs; individual fields are extracted
   with `json_extract()` for indexed reads.
3. **Separate snapshot vs history.** The current state lives in
   `workflow_runs.state_json`; immutable history lives in the per-stage
   tables (`job_scores`, `resume_reviews`, etc.) and event tables
   (`agent_events`, `llm_calls`, `step_executions`).
4. **Agents do not write directly to DB.** The orchestrator (workflow
   nodes) and FastAPI routers persist validated outputs. Per CLAUDE.md
   invariants: only the orchestrator updates `WorkflowState`; agents
   return structured Pydantic outputs.
5. **Prefer append-only for events.** Observability and auditability
   require immutable history.
6. **Additive schema evolution.** New columns are added via try/except
   `ALTER TABLE` in `init_db()`. Older readers continue to work.

---

## 3. Entity Relationships

```text
workflow_runs (one per run)
   ├── jobs                  (many; URL-deduped, persisted across runs)
   ├── resumes               (one active per user; raw_text-hash deduped)
   ├── job_scores            (one per scored (workflow, job))
   ├── review_rounds         (1-3 per (workflow, job) — reflection loop)
   ├── resume_reviews        (one per (workflow, job) — final critic+auditor output)
   ├── career_advice         (one per (workflow, job))
   ├── interview_prep        (one per (workflow, job) where threshold met)
   ├── tailored_resumes      (many per (workflow, job) — one per draft attempt)
   ├── reports               (one per workflow)
   ├── human_decisions       (many per workflow; HITL trail)
   ├── step_executions       (many per workflow; one per node entry)
   ├── agent_events          (many per workflow; per-agent lifecycle)
   ├── llm_calls             (many per workflow; per LLM call)
   ├── run_metrics           (one per workflow; aggregated rollup)
   └── security_events       (many; injection, redaction, blocked tool)

users                        (ADR-062; profile identities — id 0 = pre-existing data)
memory_items                 (per user_id; long-term learning store, isolated per profile)
user_config                  (per user_id; preference overrides)
```

`memory_items` and `user_config` are intentionally NOT scoped to a workflow
run — they outlive any single execution.

---

## 4. Core Tables

The 12 core tables that hold the workflow's domain data: workflow state,
the jobs being processed, the resume, the agent outputs at each pipeline
stage, the user's HITL decisions, and the per-user config.

---

## 4.1 workflow_runs (central table)

### Purpose

The authoritative snapshot of every workflow execution. Stores the
serialized `WorkflowState` (`state_json`), the lifecycle status, and
timestamps. Every other workflow-scoped table joins back here via
`workflow_run_id`.

### Schema

```sql
CREATE TABLE workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_type   TEXT NOT NULL,
    status          TEXT NOT NULL,
    current_step    TEXT,
    state_json      TEXT NOT NULL,
    user_id         TEXT,
    resume_id       TEXT,
    selected_job_id TEXT,
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    error_message   TEXT
);
```

### Column dictionary

| Column            | Type    | Description |
|-------------------|---------|-------------|
| `id`              | TEXT PK | UUID; the workflow_id used everywhere as `workflow_run_id`. |
| `workflow_type`   | TEXT    | e.g. `"full_career_review"`. Reserved for future workflow types. |
| `status`          | TEXT    | `running` \| `waiting_for_user` \| `completed` \| `completed_with_errors` \| `failed`. |
| `current_step`    | TEXT    | Name of the most recently entered LangGraph node. Drives the History "Stage" column. |
| `state_json`      | TEXT    | Serialized `WorkflowState` — the entire run snapshot, restorable. |
| `user_id`         | TEXT    | ADR-062: the run's owner, written at `register_run` from `state["user_id"]` (decimal-string `users.id`). Per-run tables inherit ownership transitively via `workflow_run_id`. Pre-existing rows backfilled to `"0"`. |
| `resume_id`       | TEXT    | FK → `resumes.id`. The resume the run loaded. |
| `selected_job_id` | TEXT    | Legacy; the in-graph HITL job-pick used to write here. Auto-select (ADR-054) leaves it null. |
| `started_at`      | TEXT    | ISO 8601 UTC. |
| `updated_at`      | TEXT    | ISO 8601 UTC; bumped by `register_run` and `generate_report`. |
| `completed_at`    | TEXT    | ISO 8601 UTC; null until the run reaches a terminal status. |
| `error_message`   | TEXT    | Top-level failure reason if `status` is `failed` / `completed_with_errors`. Per-step errors live in `state_json.errors[]`. |

### Workflow usage

- **Written by**: `register_run` node at workflow start (initial row), and
  `generate_report` node at workflow end (terminal status + final
  metrics). Every node update funnels through the orchestrator.
- **Read by**: `app/ui/db_reader.py::load_persisted_workflow_runs`
  (Workflow History table), `load_workflow_run` (Workflow Detail
  header), the on-demand tailoring router (reads `state_json` for
  `resume_profile` + `selected_jobs`).
- **Critical invariant** (CLAUDE.md): "register_run is the graph entry
  point. It writes the initial state (including `effective_config` and
  `custom_urls`) to `workflow_runs` so the Workflow Detail UI can show
  the settings used per run."

---

## 4.2 jobs

### Purpose

Normalized job postings from all scrapers. URL-deduplicated across runs.
ADR-057 added per-job exclusion as a pipeline filter.

### Schema

```sql
CREATE TABLE jobs (
    id                  TEXT PRIMARY KEY,
    source              TEXT,
    source_job_id       TEXT,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    job_description     TEXT,
    normalized_job_json TEXT,
    url                 TEXT,
    created_at          TEXT NOT NULL,
    excluded            INTEGER NOT NULL DEFAULT 0,   -- ADR-057
    excluded_reason     TEXT,                          -- ADR-057
    excluded_at         TEXT                           -- ADR-057
);
```

### Column dictionary

| Column                | Type        | Description |
|-----------------------|-------------|-------------|
| `id`                  | TEXT PK     | UUID generated by `JobDiscoveryService.normalize` per scrape. |
| `source`              | TEXT        | `"adzuna"` \| `"linkedin"` \| `"ladders"` \| `"manual"` (custom URL). |
| `source_job_id`       | TEXT        | Provider-native ID for traceability (Adzuna `id`, LinkedIn URN). |
| `title`               | TEXT        | Posting title. Used for analytics + search. |
| `company`             | TEXT        | Company name. Indexed (`idx_jobs_company`). |
| `location`            | TEXT        | Free-text location string from the source. |
| `job_description`     | TEXT        | Full posting text. Treated as **untrusted input** — never followed as instructions. |
| `normalized_job_json` | TEXT (JSON) | Structured `JobPosting` (Pydantic) for agent consumption. |
| `url`                 | TEXT        | Canonical posting URL. The dedup key for `JobDiscoveryService.deduplicate`. |
| `created_at`          | TEXT        | ISO 8601 UTC; first time this URL was seen. |
| `excluded`            | INT         | ADR-057. `1` = filtered from cross-run analytics + dropped at next discovery via `url_exists`. |
| `excluded_reason`     | TEXT        | ADR-057. Optional free-text recall. Never parsed by the system. |
| `excluded_at`         | TEXT        | ADR-057. ISO 8601 UTC; null on unexcluded rows. |

### Workflow usage

- **Written by**: `discover_jobs` node via `JobRepository.upsert(job)`.
  `ON CONFLICT(id) DO UPDATE` only overwrites `normalized_job_json` —
  `excluded*` columns survive re-discovery.
- **Excluded by**: `POST /jobs/{id}/exclude` endpoint
  (`app/api/routers/jobs.py`) → `JobRepository.set_excluded`.
- **Read by**: `JobDiscoveryService.deduplicate` (drops re-discovered URLs
  via `url_exists`); `db_reader.load_workflow_jobs` (Find & Score table);
  `db_reader.load_scored_jobs` (cross-run analytics — default-hides
  excluded). Tailoring router reads `jobs.id` for routing.
- **Why URL is the load-bearing dedup key** (ADR-057): Adzuna can surface
  the same posting on different days with a fresh `source_job_id` and a
  fresh row `id` would be assigned. URL is the only stable identifier.

---

## 4.2.1 users (ADR-062)

### Purpose

Profile identities for multi-user use. A profile is just an identity row; the
data it *uses* (resume, config, memory, history) lives in those tables keyed by
`user_id`. Deliberately minimal so adding authentication later is "attach a
credential to an existing row," not a data-model migration.

### Schema

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY,  -- 0 reserved for pre-existing data; new users auto-increment from 1
    name        TEXT NOT NULL,        -- display name shown in the profile selector
    note        TEXT,                 -- optional human-only label; never acted on
    created_at  TEXT NOT NULL
);
```

### Column dictionary

| Column       | Type        | Description |
|--------------|-------------|-------------|
| `id`         | INTEGER PK  | `0` is seeded by the migration as the owner of all pre-existing data. SQLite assigns the next rowid as `max(id)+1`, so `POST /users` profiles get `1, 2, 3, ...`. Stringified at the identity-seam boundary (reference columns store `"0"`, `"1"`, ...). |
| `name`       | TEXT        | Display name for the sidebar selector. Required. |
| `note`       | TEXT        | Optional human-friendly label (e.g. "New-grad SWE, west coast"). Descriptive only — the system never parses or acts on it. |
| `created_at` | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: the `init_db` migration (seeds `id = 0`, guarded) and
  `POST /users` (`UserRepository.create`, append-only).
- **Read by**: the identity seam `app/api/identity.py::get_current_user_id`
  (validates an incoming `user_id` exists) and the UI profile selector
  (`GET /users`).
- **Isolation is cooperative, not enforced** (ADR-062 Decision E): naming a
  `user_id` selects which data a request reads/writes; without authentication it
  does not *prevent* a caller from naming another profile's id. The seam is where
  a real boundary attaches if auth is added.

---

## 4.3 resumes

### Purpose

Uploaded resume files plus their parsed `ResumeProfile`. Hash-deduped so
re-uploads of the same file skip the parser.

### Schema

```sql
CREATE TABLE resumes (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT,                 -- ADR-062: owning profile (decimal-string users.id); '0' = pre-existing
    file_name           TEXT,
    raw_text            TEXT,
    raw_text_hash       TEXT,
    parsed_profile_json TEXT,
    version             INTEGER,
    is_active           INTEGER,
    created_at          TEXT NOT NULL
);
```

### Column dictionary

| Column                | Type        | Description |
|-----------------------|-------------|-------------|
| `id`                  | TEXT PK     | UUID; the `resume_id` referenced by `workflow_runs` and per-stage tables. |
| `user_id`             | TEXT        | ADR-062: the owning profile, as the decimal-string form of `users.id` (`"0"`, `"1"`, ...). Pre-existing rows were backfilled to `"0"`. |
| `file_name`           | TEXT        | Original filename (e.g. `resume.pdf`). |
| `raw_text`            | TEXT        | Extracted plain text. **Source of truth for the Fidelity Reviewer** (ADR-015 / ADR-056) — never widely exposed to agents. |
| `raw_text_hash`       | TEXT        | SHA-256 of `raw_text`. Lookup key for the parser cache (now scoped per `user_id`). |
| `parsed_profile_json` | TEXT (JSON) | Serialized `ResumeProfile` (`name`, `headline`, `summary`, `experience[]`, `skills[]`, `education[]`, `certifications[]`). What agents consume. |
| `version`             | INT         | Monotonically increasing per resume_id. Reserved for re-parse versioning. |
| `is_active`           | INT         | `1` = the current canonical resume **for that profile**; `0` = superseded. ADR-062: `create(user_id, ...)` only deactivates the same profile's prior resumes, so each profile has its own active resume. |
| `created_at`          | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `load_resume` node — `ResumeParser` parses the PDF, then
  `ResumeRepository.upsert` stores the row. If `raw_text_hash` matches an
  existing row, the cached `parsed_profile_json` is returned and Claude
  enhancement is skipped.
- **Read by**: every agent that takes resume context (Scoring, Resume
  Critic, Review Auditor, Career Advisor, Interview Coach, Tailoring,
  Fidelity Reviewer) via `state["resume_profile"]`. The tailoring router
  reads from the workflow's `state_json` rather than re-fetching here.
- **Security note**: agents receive `parsed_profile_json`, NOT `raw_text`.
  The Fidelity Reviewer is the one exception — it needs `raw_text` to
  validate that suggestions cite real resume content.

---

## 4.4 job_scores

### Purpose

The Scoring Agent's per-job output. One row per `(workflow_run_id, job_id)`.

### Schema

```sql
CREATE TABLE job_scores (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    resume_id       TEXT NOT NULL,
    score_json      TEXT NOT NULL,
    overall_score   INTEGER,
    created_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type        | Description |
|-------------------|-------------|-------------|
| `id`              | TEXT PK     | UUID. |
| `workflow_run_id` | TEXT        | FK → `workflow_runs.id`. |
| `job_id`          | TEXT        | FK → `jobs.id`. |
| `resume_id`       | TEXT        | FK → `resumes.id`. Captures which resume was scored against. |
| `score_json`      | TEXT (JSON) | Full `JobScore` Pydantic dict — `technical_score`, `architecture_score`, `leadership_score`, `domain_score`, `match_summary`, `strengths[]`, `gaps[]`, `recommended_next_action`. |
| `overall_score`   | INT         | Mirror of `score_json.overall_score` for indexed sort/filter. |
| `created_at`      | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `score_jobs` node — runs `ScoringAgent` concurrently
  across all discovered jobs (ADR-050 wrapper), persists each result
  via `ScoreRepository.create`.
- **Read by**: `await_job_selection` router for auto-select decision
  (ADR-054 — qualifies on the best track score, not overall);
  `db_reader.load_workflow_jobs` (Find & Score); analytics views
  (Top Matches, IC / Architect / Management Track, Companies);
  `report_generator` for the run report.

---

## 4.5 review_rounds

### Purpose

The Resume Critic + Review Auditor reflection loop, one row per round
(up to 3 per `(workflow, job)` per `MAX_REVIEW_ROUNDS`). Enables
auditability of the loop's convergence behavior.

### Schema

```sql
CREATE TABLE review_rounds (
    id                 TEXT PRIMARY KEY,
    workflow_run_id    TEXT NOT NULL,
    job_id             TEXT NOT NULL,
    round_number       INTEGER,
    critic_output_json TEXT,
    audit_output_json  TEXT,
    audit_score        INTEGER,
    auditor_confidence INTEGER,
    stop_reason        TEXT,
    created_at         TEXT NOT NULL
);
```

### Column dictionary

| Column               | Type        | Description |
|----------------------|-------------|-------------|
| `id`                 | TEXT PK     | UUID. |
| `workflow_run_id`    | TEXT        | FK → `workflow_runs.id`. |
| `job_id`             | TEXT        | FK → `jobs.id`. |
| `round_number`       | INT         | 1-indexed round counter. |
| `critic_output_json` | TEXT (JSON) | `ResumeReview` from Resume Critic for this round. |
| `audit_output_json`  | TEXT (JSON) | `ReviewAudit` from Review Auditor for this round. |
| `audit_score`        | INT         | Auditor's overall quality score for the critic output. |
| `auditor_confidence` | INT         | 0-100 confidence the auditor places in its own audit. |
| `stop_reason`        | TEXT        | `"converged"` \| `"max_rounds"` \| `"low_confidence"` — why the loop ended this round. |
| `created_at`         | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `deep_review` node — for each selected job, runs the
  critic / auditor / reflect loop and writes one row per round via
  `ReviewRoundsRepository`.
- **Read by**: `Workflow Detail → Review` panel for traceability;
  `report_generator` for the round-by-round transcript.

---

## 4.6 resume_reviews

### Purpose

The final consolidated `ResumeReview` per `(workflow, job)` — the output
the rest of the pipeline reads from (career advice, interview prep,
tailoring all consume this).

### Schema

```sql
CREATE TABLE resume_reviews (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    resume_id       TEXT NOT NULL,
    review_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type        | Description |
|-------------------|-------------|-------------|
| `id`              | TEXT PK     | UUID. |
| `workflow_run_id` | TEXT        | FK → `workflow_runs.id`. |
| `job_id`          | TEXT        | FK → `jobs.id`. |
| `resume_id`       | TEXT        | FK → `resumes.id`. |
| `review_json`     | TEXT (JSON) | `ResumeReview` Pydantic — `overall_fit_summary`, `critical_gaps[]`, `resume_only_gaps[]`, `career_gaps_observed[]`, `section_reviews[]`, `suggested_improvements[]`, `confidence`. |
| `created_at`      | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `deep_review` node at loop convergence (the last round's
  critic output is committed here).
- **Read by**: `career_advice` and `interview_prep` nodes (downstream
  consumers); on-demand tailoring router (`review_repo.get_review_by_run_job`);
  Workflow Detail → "Review — deep analysis & career guidance" panel.
- **Critical distinction** (ADR-013): `resume_only_gaps` = experience exists
  but is poorly expressed (fixable via tailoring). `career_gaps_observed`
  = capability genuinely missing (must NOT be fabricated). The two must
  never be conflated.

---

## 4.7 career_advice

### Purpose

The Career Advisor's per-job positioning advice.

### Schema

```sql
CREATE TABLE career_advice (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    advice_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type        | Description |
|-------------------|-------------|-------------|
| `id`              | TEXT PK     | UUID. |
| `workflow_run_id` | TEXT        | FK → `workflow_runs.id`. |
| `job_id`          | TEXT        | FK → `jobs.id`. |
| `advice_json`     | TEXT (JSON) | `CareerAdvice` Pydantic — `positioning_summary`, `recommended_next_action`, per-track lift suggestions. |
| `created_at`      | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `career_advice` node (Sonnet) per selected job.
- **Read by**: on-demand tailoring router (`advice_repo.get_advice_by_run_job`)
  for context; Workflow Detail Review panel; report.

---

## 4.8 interview_prep

### Purpose

Interview Coach output for jobs whose best track score meets the
threshold.

### Schema

```sql
CREATE TABLE interview_prep (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    prep_json       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type        | Description |
|-------------------|-------------|-------------|
| `id`              | TEXT PK     | UUID. |
| `workflow_run_id` | TEXT        | FK → `workflow_runs.id`. |
| `job_id`          | TEXT        | FK → `jobs.id`. |
| `prep_json`       | TEXT (JSON) | `InterviewPrep` Pydantic — `likely_topics[]`, `seven_day_plan[]`, `areas_to_defend[]`. |
| `created_at`      | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `interview_prep` node — only fires when the qualifying
  job's best track score ≥ `MIN_MATCH_SCORE`.
- **Read by**: Workflow Detail → "Prep — interview readiness" panel;
  report.

---

## 4.9 tailored_resumes

### Purpose

Tailoring drafts plus the Fidelity Reviewer's verdict and the user's
decision. One row per draft attempt — repeated tailoring of the same
`(workflow_run_id, job_id)` produces multiple rows ordered by
`created_at`.

### Schema

```sql
CREATE TABLE tailored_resumes (
    id                   TEXT PRIMARY KEY,
    workflow_run_id      TEXT NOT NULL,
    job_id               TEXT NOT NULL,
    resume_id            TEXT NOT NULL,
    tailored_json        TEXT NOT NULL,
    fidelity_review_json TEXT,
    decision             TEXT,
    decided_at           TEXT,
    approved             INTEGER DEFAULT 0,
    edited_json          TEXT,
    created_at           TEXT NOT NULL
);
```

### Column dictionary

| Column                 | Type        | Description |
|------------------------|-------------|-------------|
| `id`                   | TEXT PK     | UUID; the `tailoring_id` exposed by the API. |
| `workflow_run_id`      | TEXT        | FK → `workflow_runs.id`. |
| `job_id`               | TEXT        | FK → `jobs.id`. |
| `resume_id`            | TEXT        | FK → `resumes.id`. |
| `tailored_json`        | TEXT (JSON) | `TailoredResumeDraft` (ADR-056). Includes `headline_suggestions[]`, `summary_suggestions[]`, `experience_bullet_suggestions[]`, `skills_section_suggestions[]`, `overall_tailoring_notes` (strategy summary), `fidelity_risk_summary`. Each `TailoredBullet` carries `claim_type` (`reword \| emphasize \| gap \| remove`), `section_label`, `impact_rationale`, `supporting_evidence`. |
| `fidelity_review_json` | TEXT (JSON) | `FidelityReview` Pydantic. NULL only if Fidelity Reviewer raised an `LLMProviderError`. |
| `decision`             | TEXT        | `"approve"` \| `"revise"` \| `"reject"` \| `"edit"`. NULL until user decides. |
| `decided_at`           | TEXT        | ISO 8601 UTC. |
| `approved`             | INT         | Flips to `1` when `decision` is `"approve"` or `"edit"`. |
| `edited_json`          | TEXT (JSON) | Human-authored final draft, present only on an `edit` decision (ADR-059). Stored alongside the agent's original `tailored_json`, which is retained. NOT re-run through the Fidelity Reviewer. NULL otherwise. |
| `created_at`           | TEXT        | ISO 8601 UTC of draft creation. |

### Workflow usage

- **Written by**: two paths.
  1. **In-graph node** (`tailoring`): runs when
     `state["user_requested_tailoring"]` is `True` at run start.
     Currently UI-dark per CLAUDE.md.
  2. **Out-of-graph router** (ADR-055):
     `POST /workflows/{wf}/jobs/{job}/tailorings`. The current default
     path. Runs `TailoringAgent` + `FidelityReviewer` directly and
     persists via `TailoringRepository.create`.
- **Decision written by**: `POST /tailorings/{id}/decisions` →
  `TailoringRepository.set_decision`.
- **Read by**: `GET /workflows/{wf}/tailorings` and
  `GET /tailorings/{id}`; Workflow Detail → "Prep — tailored resume
  drafts" panel via `_cached_list_tailorings`.
- **Schema migrations** (ADR-055): `fidelity_review_json`, `decision`,
  `decided_at` were added via try/except `ALTER TABLE` in `init_db()`.

---

## 4.10 reports

### Purpose

The generated workflow report — markdown + structured JSON + optional
file path.

### Schema

```sql
CREATE TABLE reports (
    id                TEXT PRIMARY KEY,
    workflow_run_id   TEXT NOT NULL,
    report_json       TEXT,
    report_markdown   TEXT,
    report_file_path  TEXT,
    created_at        TEXT NOT NULL
);
```

### Column dictionary

| Column             | Type        | Description |
|--------------------|-------------|-------------|
| `id`               | TEXT PK     | UUID. |
| `workflow_run_id`  | TEXT        | FK → `workflow_runs.id`. |
| `report_json`      | TEXT (JSON) | Structured report data. |
| `report_markdown`  | TEXT        | Rendered Markdown — what the UI shows and the user downloads. |
| `report_file_path` | TEXT        | Filesystem path if the report was also written to disk. Often NULL (UI download path uses `report_markdown` directly). |
| `created_at`       | TEXT        | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `generate_report` node — final node before END. Always
  runs, even when no jobs qualified for deep review.
- **Read by**: `GET /workflows/{wf}/report`; Streamlit Run Report view.

---

## 4.11 human_decisions

### Purpose

HITL audit trail. Every decision the user made that affected the
workflow lands here for traceability — even if the same decision is
also recorded on a per-resource column elsewhere (e.g.
`tailored_resumes.decision`).

### Schema

```sql
CREATE TABLE human_decisions (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    decision_type   TEXT,
    decision_value  TEXT,
    payload_json    TEXT,
    presented_at    TEXT NOT NULL,
    decided_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type        | Description |
|-------------------|-------------|-------------|
| `id`              | TEXT PK     | UUID. |
| `workflow_run_id` | TEXT        | FK → `workflow_runs.id`. |
| `decision_type`   | TEXT        | e.g. `"approve_tailoring"`, historical: `"select_jobs_for_deep_review"`. |
| `decision_value`  | TEXT        | The decision value (e.g. `"approve"`). Free-form per type. |
| `payload_json`    | TEXT (JSON) | Optional structured context for the decision. |
| `presented_at`    | TEXT        | ISO 8601 UTC; when the UI surfaced the choice. |
| `decided_at`      | TEXT        | ISO 8601 UTC; when the user acted. The latency `decided_at - presented_at` is a useful UX metric. |

### Workflow usage

- **Written by**: in-graph HITL approvals via `DecisionRepository.create`.
  The job-selection HITL has been removed (ADR-054 auto-select); the
  in-graph tailoring approval still writes here when that path runs.
- **Not written by**: `POST /tailorings/{id}/decisions` (out-of-graph
  tailoring) — it writes only to `tailored_resumes.decision`. Both
  paths land at the same user intent; the row in `human_decisions`
  exists as a graph-correlated audit when the in-graph path fires.

---

## 4.12 user_config

### Purpose

User-overridable configuration values. Merged with `config/config.yaml`
defaults at runtime by `ConfigService`. Stores ONLY overrides — never
the full config.

### Schema

```sql
CREATE TABLE user_config (
    id                TEXT PRIMARY KEY,
    user_id           TEXT,
    config_key        TEXT NOT NULL,
    config_value_json TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
```

### Column dictionary

| Column              | Type        | Description |
|---------------------|-------------|-------------|
| `id`                | TEXT PK     | UUID. |
| `user_id`           | TEXT        | ADR-062: the profile this override belongs to (`"0"`, `"1"`, ...). `ConfigService.get_effective_config(user_id)` merges this layer over the YAML defaults. The legacy `user_id IS NULL` "system-wide" layer was migrated to `"0"`. |
| `config_key`        | TEXT        | Dotted path, e.g. `"scoring.min_match_score"`, `"agents.tailoring_agent.model"`. |
| `config_value_json` | TEXT (JSON) | Override value (always JSON-serialized for type-safety on read-back). |
| `created_at`        | TEXT        | ISO 8601 UTC. |
| `updated_at`        | TEXT        | ISO 8601 UTC; bumped on each write. |

### Workflow usage

- **Written by**: `PUT /config` endpoint via `ConfigService.set_override`.
- **Read by**: `ConfigService.get_effective_config` on every workflow
  run, merged with the YAML defaults to produce `effective_config` in
  `state_json`.
- **Restart-to-apply** (ADR-053): per-agent provider/model assignments
  require a backend restart because `ModelRegistry` caches one provider
  instance per `(provider, model)` at process start.

---

## 5. Observability Tables

Append-only event tables used by the observability service. Drive the UI's
Diagnostics panel, the cost breakdown, and live activity feed.

---

## 5.1 step_executions

### Purpose

One row per workflow node entry. The "step" name matches the LangGraph
node name (e.g. `register_run`, `discover_jobs`, `score_jobs`,
`deep_review`, `generate_report`). Drives the per-stage timing strip
and the live progress display.

### Schema

```sql
CREATE TABLE step_executions (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    step            TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    duration_ms     INTEGER,
    notes           TEXT
);
```

### Column dictionary

| Column            | Type    | Description |
|-------------------|---------|-------------|
| `id`              | TEXT PK | UUID. |
| `workflow_run_id` | TEXT    | FK → `workflow_runs.id`. |
| `step`            | TEXT    | Node name. |
| `status`          | TEXT    | `"started"` \| `"completed"` \| `"failed"`. |
| `started_at`      | TEXT    | ISO 8601 UTC. |
| `completed_at`    | TEXT    | ISO 8601 UTC; null while in-flight. |
| `duration_ms`     | INT     | Wall-clock duration set at completion. |
| `notes`           | TEXT    | Free-text status hints, e.g. error short message. |

### Workflow usage

- **Written by**: `StepRepository.start` / `.complete` / `.fail` called by
  the orchestrator on every node transition.
- **Read by**: `db_reader.load_step_executions` (Workflow Detail timeline);
  `constraint_analyzer` for "which step blew the budget" diagnostics.

---

## 5.2 agent_events

### Purpose

Per-agent lifecycle events: started / completed / failed plus an input/output
summary suitable for the Live Run Monitor. Shorter retention than `llm_calls`.

### Schema

```sql
CREATE TABLE agent_events (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_name      TEXT,
    event_type      TEXT,
    input_summary   TEXT,
    output_summary  TEXT,
    status          TEXT,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type    | Description |
|-------------------|---------|-------------|
| `id`              | TEXT PK | UUID. |
| `workflow_run_id` | TEXT    | FK → `workflow_runs.id`. |
| `agent_name`      | TEXT    | e.g. `"scoring_agent"`, `"resume_critic"`, `"tailoring_agent"`. |
| `event_type`      | TEXT    | e.g. `"started"`, `"completed"`, `"failed"`, `"unsupported_claim_detected"`. |
| `input_summary`   | TEXT    | Truncated input description (no raw inputs). |
| `output_summary`  | TEXT    | Truncated output description (no raw chain-of-thought). |
| `status`          | TEXT    | `"ok"` \| `"error"`. |
| `duration_ms`     | INT     | Wall-clock duration. |
| `created_at`      | TEXT    | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `BaseAgent._run` via `ObservabilityService.record_event`.
  Every agent inherits this — the orchestrator does not need to wire it.
- **Read by**: `db_reader.load_agent_events` (Live Run Monitor activity
  feed); per-job drill-down screen.

---

## 5.3 llm_calls

### Purpose

One row per LLM API call. The cost-truth table.

### Schema

```sql
CREATE TABLE llm_calls (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_name      TEXT,
    provider        TEXT,
    model           TEXT,
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    estimated_cost  REAL,
    latency_ms      INTEGER,
    created_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type    | Description |
|-------------------|---------|-------------|
| `id`              | TEXT PK | UUID. |
| `workflow_run_id` | TEXT    | FK → `workflow_runs.id`. |
| `agent_name`      | TEXT    | Which agent issued the call. |
| `provider`        | TEXT    | `"claude"` \| `"openai"`. |
| `model`           | TEXT    | Provider-specific model id (e.g. `"claude-sonnet-4-6"`). |
| `tokens_input`    | INT     | Prompt tokens billed. |
| `tokens_output`   | INT     | Completion tokens billed. |
| `estimated_cost`  | REAL    | USD. Computed from `model_registry`'s rate table at write time. |
| `latency_ms`      | INT     | Round-trip duration including provider-side retries. |
| `created_at`      | TEXT    | ISO 8601 UTC. |

### Workflow usage

- **Written by**: `BaseAgent._run` via `ObservabilityService.record_llm_call`
  using the typed `LLMUsage` returned by `provider.complete_with_usage`.
- **Read by**: `cost_breakdown.compute_breakdown` (Workflow Detail cost
  rollup); the "constraints hit" analyzer; analytics views that report
  total spend per run / per agent.

---

## 5.4 run_metrics

### Purpose

Aggregated per-run rollup of LLM activity and wall-clock duration. Updated
by `generate_report`.

### Schema

```sql
CREATE TABLE run_metrics (
    id                  TEXT PRIMARY KEY,
    workflow_run_id     TEXT NOT NULL,
    total_llm_calls     INTEGER,
    total_tokens_input  INTEGER,
    total_tokens_output INTEGER,
    total_cost          REAL,
    total_duration_ms   INTEGER,
    started_at          TEXT NOT NULL,
    completed_at        TEXT,
    created_at          TEXT NOT NULL
);
```

### Column dictionary

| Column                | Type    | Description |
|-----------------------|---------|-------------|
| `id`                  | TEXT PK | UUID. |
| `workflow_run_id`     | TEXT    | FK → `workflow_runs.id`. |
| `total_llm_calls`     | INT     | Count from `llm_calls` for this run. |
| `total_tokens_input`  | INT     | Sum of `llm_calls.tokens_input`. |
| `total_tokens_output` | INT     | Sum of `llm_calls.tokens_output`. |
| `total_cost`          | REAL    | USD. |
| `total_duration_ms`   | INT     | `completed_at - started_at` wall clock. |
| `started_at`          | TEXT    | ISO 8601 UTC; mirror of `workflow_runs.started_at`. |
| `completed_at`        | TEXT    | ISO 8601 UTC. |
| `created_at`          | TEXT    | ISO 8601 UTC; row creation time. |

### Workflow usage

- **Written by**: `generate_report` node — the same place that finalizes
  `workflow_runs.status`.
- **Read by**: cross-run analytics; Run History page; per-run header in
  Workflow Detail.

---

## 6. Security Table

---

## 6.1 security_events

### Purpose

Surfaces guardrail-relevant events. Append-only audit trail. Currently
narrow set of event types, room to grow.

### Schema

```sql
CREATE TABLE security_events (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    event_type      TEXT,
    severity        TEXT,
    description     TEXT,
    created_at      TEXT NOT NULL
);
```

### Column dictionary

| Column            | Type    | Description |
|-------------------|---------|-------------|
| `id`              | TEXT PK | UUID. |
| `workflow_run_id` | TEXT    | FK → `workflow_runs.id`. |
| `event_type`      | TEXT    | `prompt_injection_detected` \| `pii_redacted` \| `tool_access_blocked` \| `unsupported_claim_detected`. |
| `severity`        | TEXT    | `"info"` \| `"warning"` \| `"critical"`. |
| `description`     | TEXT    | Short free-text summary. |
| `created_at`      | TEXT    | ISO 8601 UTC. |

### Workflow usage

- **Written by**: agents and the Fidelity Reviewer through
  `ObservabilityService.record_security_event`. Index
  `idx_security_created_at` supports time-range queries.
- **Read by**: Workflow Detail Diagnostics → "Security events" expander;
  any future audit endpoint.

---

## 7. Memory Table

---

## 7.1 memory_items

### Purpose

Cross-run long-term store for preferences and learned patterns
(`MemoryService`). Intentionally NOT scoped to a workflow — survives
purges of workflow data.

### Schema

```sql
CREATE TABLE memory_items (
    id                     TEXT PRIMARY KEY,
    user_id                TEXT,              -- ADR-062: owning profile; memory is isolated per profile
    memory_type            TEXT NOT NULL,
    memory_key             TEXT,
    memory_value_json      TEXT NOT NULL,
    confidence             INTEGER,
    source_workflow_run_id TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
```

### Column dictionary

| Column                   | Type        | Description |
|--------------------------|-------------|-------------|
| `id`                     | TEXT PK     | UUID, namespaced per `user_id` to avoid key collisions across profiles. |
| `user_id`                | TEXT        | ADR-062: owning profile (`"0"`, `"1"`, ...). All `MemoryRepository` methods filter by it, so one person's learned patterns never seed another's runs. Pre-existing rows backfilled to `"0"`. |
| `memory_type`            | TEXT        | Domain bucket, e.g. `"tailoring_preference"`, `"job_dismissal_pattern"`. |
| `memory_key`             | TEXT        | Optional sub-key for upsert semantics. |
| `memory_value_json`      | TEXT (JSON) | Structured value. **Never raw LLM text** — must be normalized. |
| `confidence`             | INT         | 0-100; how confident the system is in this item. |
| `source_workflow_run_id` | TEXT        | The run that produced this learning, for traceability. Nullable so memory can be seeded externally. |
| `created_at`             | TEXT        | ISO 8601 UTC. |
| `updated_at`             | TEXT        | ISO 8601 UTC; bumped on upsert. |

### Workflow usage

- **Written by**: `MemoryService.upsert` from agent post-processing or
  end-of-run summarization.
- **Read by**: agents that load memory context (`MemoryService.fetch_for_agent`).
- **Hard rules** (CLAUDE.md / state_and_memory_model):
  - Memory must be structured.
  - Memory must not store raw LLM outputs.
  - Memory must be selectively retrieved (no "load everything").
  - Confidence + retention drive eviction.

---

## 8. Indexing Strategy

Indexes created in `init_db()`:

```sql
CREATE INDEX idx_workflow_runs_status     ON workflow_runs(status);
CREATE INDEX idx_workflow_runs_started_at ON workflow_runs(started_at);
CREATE INDEX idx_jobs_company             ON jobs(company);
CREATE INDEX idx_jobs_title               ON jobs(title);
CREATE INDEX idx_jobs_created_at          ON jobs(created_at);
CREATE INDEX idx_job_scores_score         ON job_scores(overall_score);
CREATE INDEX idx_step_executions_run      ON step_executions(workflow_run_id);
CREATE INDEX idx_step_executions_started  ON step_executions(started_at);
CREATE INDEX idx_review_rounds_run        ON review_rounds(workflow_run_id);
CREATE INDEX idx_agent_events_run         ON agent_events(workflow_run_id);
CREATE INDEX idx_agent_events_created_at  ON agent_events(created_at);
CREATE INDEX idx_llm_calls_run            ON llm_calls(workflow_run_id);
CREATE INDEX idx_llm_calls_created_at     ON llm_calls(created_at);
CREATE INDEX idx_memory_type              ON memory_items(memory_type);
CREATE INDEX idx_memory_updated_at        ON memory_items(updated_at);
CREATE INDEX idx_security_created_at      ON security_events(created_at);
-- ADR-062: per-user read scoping. workflow_runs index lives in the base schema;
-- the resumes / memory_items indexes are created AFTER their additive ALTER
-- (executescript runs before the ALTERs on a pre-existing DB).
CREATE INDEX idx_workflow_runs_user       ON workflow_runs(user_id);
CREATE INDEX idx_resumes_user             ON resumes(user_id);
CREATE INDEX idx_memory_user              ON memory_items(user_id);
```

There is intentionally no index on `jobs.url` — `JobDiscoveryService.deduplicate`
uses a per-URL `SELECT 1 LIMIT 1` which SQLite handles well at our row
counts (low thousands). Add a `UNIQUE(url)` constraint if URL volume grows
substantially.

There is intentionally no index on `jobs.excluded` — exclusion checks happen
at read-time with a small `WHERE` clause; the table is small enough that
the planner does fine without it.

---

## 9. JSON Storage Strategy

Most agent outputs are stored as JSON columns:

```text
workflow_runs.state_json
jobs.normalized_job_json
resumes.parsed_profile_json
job_scores.score_json
review_rounds.critic_output_json / audit_output_json
resume_reviews.review_json
career_advice.advice_json
interview_prep.prep_json
tailored_resumes.tailored_json / fidelity_review_json
reports.report_json
human_decisions.payload_json
user_config.config_value_json
memory_items.memory_value_json
```

### Rules

- **Validate before insert.** Every JSON write should serialize a Pydantic
  model (`model_dump()`); never raw `dict[Any]` from agents.
- **Avoid deep nesting.** Two-three levels max; use side tables for
  high-cardinality children.
- **Versionable.** Add a `_version` field to JSON values when a breaking
  shape change is unavoidable.
- **No hidden chain-of-thought.** Store summaries, not raw reasoning.
- **Extract for indexed reads.** Use SQLite's `json_extract()` in
  per-row read queries — see `db_reader.py` for the pattern.

---

## 10. Data Flow Summary

```text
register_run                         → workflow_runs (initial state row)
                                       step_executions (started)
        ↓
discover_jobs                        → jobs (URL-deduped upsert)
                                       step_executions, agent_events
        ↓
load_resume                          → resumes (hash-deduped upsert)
                                       step_executions
        ↓
score_jobs (concurrent per job)      → job_scores
                                       agent_events, llm_calls
        ↓
await_job_selection (auto-select)    → no DB writes (state-only;
                                       ADR-054 removed the HITL pause)
        ↓
deep_review (per selected job)       → review_rounds (1-3 rows per job)
                                       resume_reviews (final consolidated)
                                       agent_events, llm_calls
        ↓
career_advice (per selected job)     → career_advice
                                       agent_events, llm_calls
        ↓
interview_prep (threshold-gated)     → interview_prep
                                       agent_events, llm_calls
        ↓
generate_report                      → reports
                                       run_metrics (rollup)
                                       workflow_runs (terminal status)
                                       step_executions (completed)

(post-workflow, on demand)
POST /workflows/{wf}/jobs/{job}/tailorings (ADR-055)
                                     → tailored_resumes (one row per draft)
                                       agent_events, llm_calls
POST /tailorings/{id}/decisions
                                     → tailored_resumes.decision

(any time, by user action)
POST /jobs/{id}/exclude (ADR-057)    → jobs.excluded = 1

(opportunistic across all agents)
                                     → security_events (guardrail hits)
                                     → memory_items (preference learning)
```

---

## 11. Schema Evolution Strategy

SQLite schema changes:

- **Add columns, never remove.** Use try/except `ALTER TABLE ... ADD COLUMN`
  inside `init_db()` so existing databases pick the columns up
  automatically (precedent: ADR-055 for `tailored_resumes`, ADR-057 for
  `jobs.excluded*`).
- **Default values matter.** New columns must have a sensible `DEFAULT`
  so existing rows remain valid.
- **JSON shape changes.** Bump a `_version` field inside the JSON
  payload, not the column name. Older readers ignore unknown fields.
- **Backward compatibility for `WorkflowState`.** New keys in
  `state_json` must default-initialize on read so older rows can still
  be deserialized.

---

## 12. Anti-Patterns to Avoid

- Agents writing directly to the database (orchestrator-only).
- Storing raw LLM responses without structure.
- Storing entire prompts unnecessarily — they're available in the prompt
  files.
- Mixing snapshot (`workflow_runs.state_json`) with event history
  (`agent_events`, `llm_calls`).
- Over-normalizing JSON-heavy data.
- Storing hidden chain-of-thought.
- Uncontrolled memory growth (bypass the confidence + retention rules).

---

## 13. Final Principle

The data model should make it possible to answer:

```text
What happened?           → step_executions, agent_events
Why did it happen?       → state_json + per-stage outputs
What did the system decide?   → job_scores, review_rounds, advice, prep
What did the user decide?     → human_decisions, tailored_resumes.decision,
                                jobs.excluded
What did the model produce?   → review_json, tailored_json, prep_json
How much did it cost?    → llm_calls, run_metrics
Was it safe and correct? → security_events, fidelity_review_json
```

If the data model cannot answer these questions, it is incomplete.
