# Phase 1 — Foundation

> **Status:** Approved — 2026-04-28
>
> **Approved by:** Sivakumar Suthram
>
> **Review gate passed:** Schemas, DB table definitions, timestamp strategy, data retention policy,
> and ConfigService design reviewed and confirmed before implementation began.

---

## What is Phase 1?

Phase 1 builds the **data contracts and persistence layer** that every other phase depends on.

Nothing intelligent happens here — no LLM calls, no agents, no workflows. The sole purpose is to establish:

- what data looks like as it flows through the system
- how it is stored
- how configuration is loaded and merged

Get this right and all future phases build on a solid, validated foundation. Get it wrong and misalignments compound through every agent, workflow, and test written afterward.

---

## Why this order?

The implementation is **bottom-up by design**:

```
Phase 1: Foundation   ← you are here
Phase 2: Services
Phase 3: LLM Provider
Phase 4: Agents
Phase 5: Orchestrator
Phase 6: UI
```

Agents (Phase 4) return structured outputs that are validated against schemas (Phase 1).
The orchestrator (Phase 5) updates WorkflowState (Phase 1).
Services (Phase 2) use the repositories (Phase 1).

If the schemas are not correct, everything that references them is wrong. Phase 1 is the foundation, not a formality.

---

## Deliverables

Phase 1 has four groups of deliverables:

| Group | What | File Location |
|---|---|---|
| A | Workflow State types | `app/state/workflow_state.py` |
| B | Agent output schemas | `app/schemas/` |
| C | SQLite tables + migrations | `app/repositories/` |
| D | Config service | `app/services/config_service.py` + `config/config.yaml` |

> **Note on timestamps and retention:** Every type that represents execution — steps, metrics, errors — carries its own timestamps. Every DB table carries `created_at`. Step-level timing is tracked in a dedicated `step_executions` table, enabling both the UI execution timeline and efficient data retention purges. Retention policy is defined in `config.yaml` and enforced by the repository layer.

---

## Group A — Workflow State

**File:** `app/state/workflow_state.py`

### What is WorkflowState?

`WorkflowState` is the **single source of truth** for a running workflow. It captures everything the system knows and has done during one execution:

- what step is currently running
- what jobs have been found, scored, selected
- what the resume looks like
- what agents have produced
- what the user has decided
- what errors have occurred
- how much the run has cost

It is the working memory of the entire execution. Only the orchestrator reads and writes it. Agents never touch it directly — they receive selected portions as input, and return structured outputs that the orchestrator validates and merges back in.

### The types to implement

#### `WorkflowStatus`

An enum of valid lifecycle states for a workflow run:

| Value | Meaning |
|---|---|
| `initialized` | Workflow created, not yet started |
| `running` | Actively executing |
| `waiting_for_user` | Paused at a HITL checkpoint |
| `completed` | Finished successfully |
| `failed` | Terminated due to error |
| `cancelled` | Stopped by user or system |

These values are used consistently in DB storage, UI display, and API responses. They are not free-form strings.

#### `WorkflowStep`

An enum of named steps within a workflow execution:

```
initialized
job_discovery
resume_profile_loading
scoring
awaiting_job_selection
research
resume_critique
review_audit
reflection_decision
career_advice
interview_prep
tailoring
fidelity_review
awaiting_user_approval
report_generation
completed
failed
```

These tell the system (and the UI) exactly where execution is at any moment, and are recorded in observability events to build the execution timeline.

#### `RunMetrics`

A Pydantic model capturing cost and performance for a workflow run:

```python
class RunMetrics(BaseModel):
    llm_calls: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    estimated_cost_usd: float = 0.0
    total_duration_ms: int = 0
    started_at: str | None = None    # ISO 8601 — set when workflow begins
    completed_at: str | None = None  # ISO 8601 — set when workflow ends or fails
```

`started_at` and `completed_at` are set at the run level so cost and duration are self-contained in this object. `total_duration_ms` is derived from them but stored explicitly for fast querying. Updated incrementally as agents run; enforces `MAX_LLM_CALLS_PER_RUN`.

#### `WorkflowError`

A Pydantic model for recording errors without crashing the workflow:

```python
class WorkflowError(BaseModel):
    step: str
    error_type: str
    message: str
    recoverable: bool
    occurred_at: str             # ISO 8601 — when the error happened
    suggested_action: str | None = None
```

`occurred_at` is stamped at the moment the error is caught, so the error log can be correlated with the step timeline. Recoverable errors (e.g., a scraper that gets blocked) are recorded and the workflow continues. Non-recoverable errors move the workflow to `failed`.

#### `HumanDecision`

A Pydantic model recording what the user decided at a HITL checkpoint:

```python
class HumanDecision(BaseModel):
    decision_type: str
    decision_value: str
    payload: dict = {}
    presented_at: str     # ISO 8601 — when the UI showed the checkpoint to the user
    decided_at: str       # ISO 8601 — when the user submitted their decision
```

`presented_at` vs `decided_at` lets the system measure how long the user took to make a decision — useful for UX analysis and for detecting abandoned workflows where `pending_decision` was set but never resolved. Examples of `decision_type`: `select_jobs_for_deep_review`, `approve_tailoring`, `reject_tailoring`.

#### `StepExecution`

A Pydantic model recording when each workflow step ran and how it ended:

```python
class StepExecution(BaseModel):
    step: WorkflowStep
    status: str               # started | completed | failed | skipped
    started_at: str           # ISO 8601
    completed_at: str | None = None   # ISO 8601 — None if still running or failed mid-step
    duration_ms: int | None = None    # derived from started_at/completed_at, stored explicitly
    notes: str | None = None          # e.g. stop reason, skip reason, error summary
```

The orchestrator appends a `StepExecution` to `step_history` when a step begins (`status = "started"`) and updates it when the step ends. This gives a full, timestamped execution timeline for every workflow run — which the UI uses for the observability screen and which the DB uses for cross-run performance analysis.

Without this, you cannot answer: *"How long did the scoring step take?"* or *"At what time did this run pause for user input?"*

---

#### `WorkflowState`

The main container. All of the above types live inside it:

```python
class WorkflowState(BaseModel):
    # Identity
    workflow_id: str
    workflow_type: str
    status: WorkflowStatus
    current_step: WorkflowStep

    user_id: str | None = None

    # Resume
    resume_id: str | None = None
    resume_profile: dict | None = None
    resume_version: int | None = None

    # Jobs
    search_criteria: dict = {}
    raw_jobs: list[dict] = []
    normalized_jobs: list[dict] = []
    scored_jobs: list[dict] = []
    selected_jobs: list[dict] = []

    # Research
    research_context: dict | None = None
    skill_gaps: dict = {}

    # Review loop
    review_rounds: list[dict] = []
    final_resume_review: dict | None = None

    # Career intelligence
    career_advice: dict | None = None
    interview_prep: dict | None = None
    tailored_resume: dict | None = None
    fidelity_review: dict | None = None

    # HITL
    pending_decision: dict | None = None
    human_decisions: list[HumanDecision] = []

    # Report
    report: dict | None = None

    # Execution tracking
    step_history: list[StepExecution] = []   # ordered log of every step with timestamps
    run_metrics: RunMetrics = RunMetrics()
    errors: list[WorkflowError] = []

    # Config snapshot
    effective_config: dict = {}

    # Timestamps (run-level)
    created_at: str     # ISO 8601 — set once at workflow creation
    updated_at: str     # ISO 8601 — refreshed on every state write
```

### Key design rules

- `pending_decision` is set when the workflow pauses for user input. The backend clears it when the user responds.
- `review_rounds` is a list — every iteration of the Critic/Auditor loop is appended, not overwritten. This gives full traceability of whether critique improved over rounds.
- `step_history` is append-only. The orchestrator adds a new `StepExecution` at the start of each step and updates `completed_at`/`duration_ms` when it ends. It is never pruned within a run.
- `effective_config` is a snapshot of the configuration used for this run. It is injected at workflow start and does not change mid-run.
- `updated_at` is the run-level last-modified timestamp. It is always refreshed on every state write, even if the step has not changed.

---

## Group B — Agent Output Schemas

**Directory:** `app/schemas/`

### Why do we need these schemas?

Every agent in the system returns a **structured output** — not free text. These Pydantic schemas define exactly what shape that output must have.

The orchestrator validates agent output against these schemas before:
- merging it into WorkflowState
- persisting it to the database
- returning it to the UI

If an agent returns output that fails schema validation, the orchestrator retries or records a recoverable error. Nothing unvalidated enters the system.

### The 8 schemas to implement

#### 1. `JobScore` — `app/schemas/job_score.py`

Produced by: **Scoring Agent**

```python
class JobScore(BaseModel):
    job_id: str
    resume_id: str
    overall_score: int          # 0–100
    technical_score: int        # 0–100
    architecture_score: int     # 0–100
    leadership_score: int       # 0–100
    domain_score: int           # 0–100
    match_summary: str
    strengths: list[str]
    gaps: list[str]
    recommended_next_action: str
    confidence: int             # 0–100
```

All scores are integers 0–100. The orchestrator uses `overall_score` to rank jobs and decide which go to deep review.

---

#### 2. `ResearchContext` — `app/schemas/research_context.py`

Produced by: **Research Agent**

```python
class ResearchStep(BaseModel):
    step_number: int
    tool_used: str
    observation_summary: str

class ResearchContext(BaseModel):
    job_id: str
    company_summary: str
    role_context: str
    technology_signals: list[str]
    leadership_signals: list[str]
    domain_signals: list[str]
    risk_flags: list[str]
    research_steps: list[ResearchStep]
    confidence: int             # 0–100
```

`research_steps` records what the Research Agent actually did (bounded to `MAX_RESEARCH_STEPS = 2`). This is a summary trace — not raw chain-of-thought.

---

#### 3. `ResumeReview` — `app/schemas/resume_review.py`

Produced by: **Resume Critic Agent**

```python
class SectionReview(BaseModel):
    section_name: str
    current_issue: str
    why_it_matters: str
    improvement_opportunity: str
    suggested_direction: str
    evidence: str
    risk_level: str             # low, medium, high

class ResumeReview(BaseModel):
    job_id: str
    resume_id: str
    overall_fit_summary: str
    section_reviews: list[SectionReview]
    critical_gaps: list[str]
    resume_only_gaps: list[str]
    career_gaps_observed: list[str]
    suggested_improvements: list[str]
    questions_for_user: list[str]
    confidence: int             # 0–100
```

The separation of `resume_only_gaps` from `career_gaps_observed` is fundamental — the system must distinguish "you have this experience but haven't expressed it well" from "you genuinely haven't done this."

---

#### 4. `ReviewAudit` — `app/schemas/review_audit.py`

Produced by: **Review Auditor Agent**

```python
class ReviewAudit(BaseModel):
    job_id: str
    round_number: int
    audit_score: int            # 0–100
    auditor_confidence: int     # 0–100
    quality_summary: str
    missing_analysis_points: list[str]
    generic_or_weak_feedback: list[str]
    unsupported_claims: list[str]
    fidelity_concerns: list[str]
    recommended_revision_instructions: list[str]
    stop_recommendation: bool
    stop_reason: str | None = None
```

`stop_recommendation` is the auditor's signal to the orchestrator that the reflection loop should stop — either because the critique is good enough or because further iteration is unlikely to improve it (stagnation detection).

---

#### 5. `CareerAdvice` — `app/schemas/career_advice.py`

Produced by: **Career Advisor Agent**

```python
class CareerAdvice(BaseModel):
    job_id: str
    positioning_summary: str
    resume_gaps: list[str]
    career_gaps: list[str]
    role_fit_assessment: str
    recommended_positioning: str
    skills_to_strengthen: list[str]
    experience_to_collect: list[str]
    thirty_sixty_ninety_day_plan: list[str]
    recommended_next_action: str
    confidence: int             # 0–100
```

`resume_gaps` vs `career_gaps` again enforces the core distinction: one is an expression problem, the other is a real development gap.

---

#### 6. `InterviewPrep` — `app/schemas/interview_prep.py`

Produced by: **Interview Coach Agent**

```python
class InterviewPrep(BaseModel):
    job_id: str
    likely_interview_topics: list[str]
    technical_topics_to_review: list[str]
    leadership_stories_to_prepare: list[str]
    weak_areas_to_defend: list[str]
    questions_to_ask_interviewer: list[str]
    seven_day_prep_plan: list[str]
    confidence: int             # 0–100
```

---

#### 7. `TailoredResumeDraft` — `app/schemas/tailored_resume_draft.py`

Produced by: **Tailoring Agent**

```python
class TailoredBullet(BaseModel):
    original_text: str
    suggested_text: str
    supporting_evidence: str    # must reference original resume
    claim_type: str             # reword | emphasize | gap
    fidelity_risk: str          # low | medium | high
    unsupported_claims: list[str]

class TailoredResumeDraft(BaseModel):
    job_id: str
    resume_id: str
    summary_suggestions: list[TailoredBullet]
    experience_bullet_suggestions: list[TailoredBullet]
    skills_section_suggestions: list[str]
    overall_tailoring_notes: str
    fidelity_risk_summary: str
```

`supporting_evidence` is mandatory on every bullet. Every change must be traceable to something in the original resume. If the experience doesn't exist, `claim_type = "gap"` — never rewritten as if it does.

---

#### 8. `FidelityReview` — `app/schemas/fidelity_review.py`

Produced by: **Fidelity Reviewer Agent**

```python
class FidelityReview(BaseModel):
    job_id: str
    resume_id: str
    overall_fidelity_status: str    # pass | fail | needs_revision
    unsupported_claims: list[str]
    fabricated_metrics: list[str]
    inflated_scope_flags: list[str]
    unsupported_technology_flags: list[str]
    unsupported_certification_flags: list[str]
    required_removals: list[str]
    required_revisions: list[str]
    approval_recommendation: str    # approve | revise | reject
    confidence: int                 # 0–100
```

This schema is the final safety net before any tailored content reaches the user. `approval_recommendation = "reject"` means the draft must not be presented.

---

## Group C — SQLite Tables

**Directory:** `app/repositories/`

### Design approach

- Raw `sqlite3` — no SQLAlchemy
- All timestamps are ISO 8601 strings stored as `TEXT NOT NULL` — human readable in any SQLite viewer, correct lexicographic sort order, compatible with SQLite's built-in `datetime()` / `strftime()` functions
- Every timestamp in the system is produced by a single shared utility in `database.py`:
  ```python
  def utcnow_iso() -> str:
      return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
      # always: 2026-04-28T14:30:00.123Z
  ```
  No repository or service may produce a timestamp by any other means. This enforces format consistency system-wide, which is the only requirement for TEXT timestamps to sort and compare correctly.
- JSON columns store serialized Pydantic models
- Migrations are versioned SQL scripts
- Agents never write to the DB — only the orchestrator does, through repositories

### The 17 tables

#### Core workflow tables

| Table | Purpose |
|---|---|
| `workflow_runs` | Central table — one row per execution, stores state snapshot |
| `jobs` | Normalized job postings from all sources |
| `resumes` | Uploaded resumes and their parsed profiles |
| `job_scores` | Scoring Agent output per job per run |

#### Deep review tables

| Table | Purpose |
|---|---|
| `review_rounds` | One row per Critic/Auditor iteration — traces reflection loop |
| `resume_reviews` | Final Resume Critic output (after loop ends) |
| `career_advice` | Career Advisor output |
| `interview_prep` | Interview Coach output |
| `tailored_resumes` | Tailoring Agent drafts — includes approval status |
| `reports` | Final assembled reports (Markdown, DOCX paths, JSON) |

#### HITL and config tables

| Table | Purpose |
|---|---|
| `human_decisions` | Every user decision at a HITL checkpoint — includes `presented_at` and `decided_at` |
| `user_config` | User preference overrides (merged with YAML at runtime) |

#### Observability tables

| Table | Purpose |
|---|---|
| `step_executions` | One row per workflow step per run — timestamped start, end, duration, status |
| `agent_events` | Every agent start/complete/fail event |
| `llm_calls` | Per-call token usage, cost, latency |
| `run_metrics` | Rolled-up totals for a workflow run — includes `started_at` and `completed_at` |

**Why `step_executions` is separate from `agent_events`:** `agent_events` records individual LLM agent calls. `step_executions` records workflow-level step transitions. A single step (e.g., `deep_review`) may trigger multiple agent calls. These are different levels of granularity and serve different purposes: `step_executions` drives the UI execution timeline; `agent_events` drives per-agent cost and latency analysis.

#### Safety tables

| Table | Purpose |
|---|---|
| `security_events` | Prompt injection detections, PII events, access blocks |

#### Memory table

| Table | Purpose |
|---|---|
| `memory_items` | Structured long-term learning across runs |

### Full SQL definitions

```sql
-- workflow_runs
CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    state_json TEXT NOT NULL,
    user_id TEXT,
    resume_id TEXT,
    selected_job_id TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

-- jobs
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    source TEXT,
    source_job_id TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    job_description TEXT,
    normalized_job_json TEXT,
    url TEXT,
    created_at TEXT NOT NULL
);

-- resumes
CREATE TABLE resumes (
    id TEXT PRIMARY KEY,
    file_name TEXT,
    raw_text TEXT,
    parsed_profile_json TEXT,
    version INTEGER,
    is_active INTEGER,
    created_at TEXT NOT NULL
);

-- job_scores
CREATE TABLE job_scores (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    score_json TEXT NOT NULL,
    overall_score INTEGER,
    created_at TEXT NOT NULL
);

-- review_rounds
CREATE TABLE review_rounds (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    round_number INTEGER,
    critic_output_json TEXT,
    audit_output_json TEXT,
    audit_score INTEGER,
    auditor_confidence INTEGER,
    stop_reason TEXT,
    created_at TEXT NOT NULL
);

-- resume_reviews
CREATE TABLE resume_reviews (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    review_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- career_advice
CREATE TABLE career_advice (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    advice_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- interview_prep
CREATE TABLE interview_prep (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    prep_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- tailored_resumes
CREATE TABLE tailored_resumes (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    tailored_json TEXT NOT NULL,
    approved INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- reports
CREATE TABLE reports (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    report_json TEXT,
    report_markdown TEXT,
    report_file_path TEXT,
    created_at TEXT NOT NULL
);

-- human_decisions
CREATE TABLE human_decisions (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    decision_type TEXT,
    decision_value TEXT,
    payload_json TEXT,
    presented_at TEXT NOT NULL,   -- when the UI showed the checkpoint to the user
    decided_at TEXT NOT NULL      -- when the user submitted their decision
);

-- user_config
CREATE TABLE user_config (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    config_key TEXT NOT NULL,
    config_value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- step_executions
CREATE TABLE step_executions (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    step TEXT NOT NULL,           -- WorkflowStep enum value
    status TEXT NOT NULL,         -- started | completed | failed | skipped
    started_at TEXT NOT NULL,     -- ISO 8601
    completed_at TEXT,            -- ISO 8601 — null if step failed or still running
    duration_ms INTEGER,          -- derived, stored for fast querying
    notes TEXT                    -- stop reason, skip reason, error summary
);

-- agent_events
CREATE TABLE agent_events (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_name TEXT,
    event_type TEXT,
    input_summary TEXT,
    output_summary TEXT,
    status TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

-- llm_calls
CREATE TABLE llm_calls (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_name TEXT,
    provider TEXT,
    model TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    estimated_cost REAL,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

-- run_metrics
CREATE TABLE run_metrics (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    total_llm_calls INTEGER,
    total_tokens_input INTEGER,
    total_tokens_output INTEGER,
    total_cost REAL,
    total_duration_ms INTEGER,
    started_at TEXT NOT NULL,     -- workflow start time (mirrors workflow_runs.started_at)
    completed_at TEXT,            -- workflow end time (null if still running or failed)
    created_at TEXT NOT NULL
);

-- security_events
CREATE TABLE security_events (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    event_type TEXT,
    severity TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

-- memory_items
CREATE TABLE memory_items (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    memory_key TEXT,
    memory_value_json TEXT NOT NULL,
    confidence INTEGER,
    source_workflow_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### Indexes

```sql
-- workflow lookup and status filtering
CREATE INDEX idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX idx_workflow_runs_started_at ON workflow_runs(started_at);   -- retention purge

-- job lookup
CREATE INDEX idx_jobs_company ON jobs(company);
CREATE INDEX idx_jobs_title ON jobs(title);
CREATE INDEX idx_jobs_created_at ON jobs(created_at);                      -- retention purge

-- score ranking
CREATE INDEX idx_job_scores_score ON job_scores(overall_score);

-- observability queries (by run)
CREATE INDEX idx_step_executions_run ON step_executions(workflow_run_id);
CREATE INDEX idx_step_executions_started_at ON step_executions(started_at); -- retention purge
CREATE INDEX idx_review_rounds_run ON review_rounds(workflow_run_id);
CREATE INDEX idx_agent_events_run ON agent_events(workflow_run_id);
CREATE INDEX idx_agent_events_created_at ON agent_events(created_at);       -- retention purge
CREATE INDEX idx_llm_calls_run ON llm_calls(workflow_run_id);
CREATE INDEX idx_llm_calls_created_at ON llm_calls(created_at);             -- retention purge

-- memory retrieval
CREATE INDEX idx_memory_type ON memory_items(memory_type);
CREATE INDEX idx_memory_updated_at ON memory_items(updated_at);             -- retention purge

-- security audit
CREATE INDEX idx_security_events_created_at ON security_events(created_at); -- retention purge (longer window)
```

Tables marked `-- retention purge` require a time-column index because the purge query is a DELETE WHERE `created_at < cutoff`, which becomes a full table scan without it.

### Repository classes to implement

Each table gets a repository class:

```
app/repositories/workflow_repository.py      ← workflow_runs
app/repositories/job_repository.py           ← jobs
app/repositories/resume_repository.py        ← resumes
app/repositories/score_repository.py         ← job_scores
app/repositories/review_repository.py        ← review_rounds, resume_reviews
app/repositories/advice_repository.py        ← career_advice, interview_prep
app/repositories/tailoring_repository.py     ← tailored_resumes
app/repositories/report_repository.py        ← reports
app/repositories/decision_repository.py      ← human_decisions
app/repositories/config_repository.py        ← user_config
app/repositories/step_repository.py          ← step_executions
app/repositories/observability_repository.py ← agent_events, llm_calls, run_metrics
app/repositories/security_repository.py      ← security_events
app/repositories/memory_repository.py        ← memory_items
```

There will also be a shared DB connection helper:

```
app/repositories/database.py               ← connection, init, migration runner, purge utility
```

---

## Group D — ConfigService

**Files:**
- `config.yaml` — system defaults
- `app/services/config_service.py` — merge logic

### What does ConfigService do?

Configuration in v2 comes from two sources:

1. **`config.yaml`** — static file checked into the repo. Contains system defaults and system limits. Never editable by users.
2. **`user_config` DB table** — user-specific overrides stored at runtime. Editable through the UI settings screen.

`ConfigService` merges these two sources at runtime using a simple priority rule:

```
User DB Overrides  →  highest priority
YAML Defaults      →  fallback
Hardcoded Limits   →  enforced floor/ceiling
```

### What goes in `config.yaml`

```yaml
llm:
  default_model: claude-sonnet-4-6
  scoring_model: claude-haiku-4-5-20251001
  provider: anthropic

search:
  max_jobs: 20

limits:
  max_selected_jobs: 3
  max_research_steps: 2
  max_review_rounds: 3
  max_llm_calls_per_job: 10
  max_llm_calls_per_run: 50

scoring:
  deep_review_threshold: 70
  interview_prep_threshold: 75

tailoring:
  style: conservative

retention:
  workflow_runs_days: 90         # keep workflow run state and outputs for 90 days
  observability_days: 30         # agent_events, llm_calls, step_executions
  security_events_days: 180      # security events kept longer for audit purposes
  memory_items_days: 365         # memory is long-lived — keep for a year by default
  jobs_days: 90                  # job postings (they go stale anyway)
```

**Why per-table retention windows?** Different data has different value over time. Security events need a longer audit trail. Raw observability data (LLM call logs, step traces) accumulates fast and has less value after 30 days. Memory items are the system's long-term learning and should outlive individual runs by a wide margin.

### What users can override (via DB)

- `search.roles` — job titles to search for
- `search.locations` — locations to search in
- `search.max_jobs` — up to the system max of 20
- `scoring.weights` — relative importance of each score dimension
- `tailoring.style` — conservative or standard

### What users can never override

- `llm.default_model` — LLM model selection
- `llm.provider` — provider selection
- `limits.*` — all execution limits
- `scoring.deep_review_threshold` — safety thresholds

### The merge function

```python
class ConfigService:
    def get_effective_config(self, user_id: str) -> dict:
        yaml_config = self._load_yaml()
        user_overrides = self._load_user_overrides(user_id)
        merged = self._merge(yaml_config, user_overrides)
        return self._enforce_limits(merged)

    def _enforce_limits(self, config: dict) -> dict:
        # User cannot raise max_jobs above system max
        config["search"]["max_jobs"] = min(
            config["search"]["max_jobs"],
            SYSTEM_MAX_JOBS
        )
        return config
```

The effective config is then injected into `WorkflowState.effective_config` at workflow start.

### Data retention and purge

The `database.py` module exposes a `purge_old_data(config: dict)` utility that reads the `retention.*` values from effective config and runs DELETE statements against each table:

```python
def purge_old_data(config: dict) -> dict[str, int]:
    """
    Delete rows older than configured retention windows.
    Returns a dict of {table_name: rows_deleted} for logging.
    """
    cutoffs = {
        "workflow_runs":   days_ago(config["retention"]["workflow_runs_days"]),
        "step_executions": days_ago(config["retention"]["observability_days"]),
        "agent_events":    days_ago(config["retention"]["observability_days"]),
        "llm_calls":       days_ago(config["retention"]["observability_days"]),
        "security_events": days_ago(config["retention"]["security_events_days"]),
        "memory_items":    days_ago(config["retention"]["memory_items_days"]),
        "jobs":            days_ago(config["retention"]["jobs_days"]),
    }
    # DELETE WHERE created_at < cutoff for each table
    ...
```

Key rules:
- Purge is **explicit** — it does not run automatically. It is called by a maintenance endpoint or by the user from the UI settings screen.
- Purging `workflow_runs` cascades intent only — child tables (`job_scores`, `review_rounds`, etc.) are purged independently by their own `created_at` window.
- `memory_items` are never purged in the same pass as observability data — they have a separate, longer retention window.
- Purge results are logged as a `security_event` for auditability.

---

## Tests for Phase 1

All tests live in `tests/`. No real LLM calls.

### Schema tests (`tests/test_schemas.py`)

- `WorkflowState` validates correctly with all required fields
- `WorkflowState` rejects missing `workflow_id`
- `WorkflowStatus` rejects invalid status values
- `WorkflowStep` rejects unknown step values
- `StepExecution` requires `step`, `status`, and `started_at`
- `StepExecution` with `status = "completed"` requires `completed_at` and `duration_ms`
- `RunMetrics` stores `started_at` and `completed_at` correctly
- `WorkflowError` includes `occurred_at` timestamp
- `HumanDecision` includes both `presented_at` and `decided_at`
- `JobScore` rejects scores outside 0–100 range
- `ResumeReview` validates correctly
- `TailoredBullet` rejects missing `supporting_evidence`
- `FidelityReview` rejects invalid `approval_recommendation` values

### DB tests (`tests/test_repositories.py`)

- All 18 tables are created correctly by the migration runner
- `workflow_runs` insert and fetch round-trip correctly
- `step_executions` insert stores `started_at`, `completed_at`, `duration_ms`
- `step_executions` fetch by `workflow_run_id` returns correct ordered steps
- `job_scores` insert stores `score_json` and `overall_score`
- `review_rounds` insert stores both critic and audit JSON
- `run_metrics` insert stores `started_at` and `completed_at`
- `human_decisions` insert stores `presented_at` and `decided_at`
- `user_config` insert and fetch round-trip correctly
- `purge_old_data` deletes rows older than configured cutoff
- `purge_old_data` does not delete rows within the retention window
- `purge_old_data` returns correct row counts per table

### ConfigService tests (`tests/test_config_service.py`)

- YAML defaults load correctly including `retention.*` values
- User overrides merge over YAML defaults
- User cannot exceed `SYSTEM_MAX_JOBS` limit
- User cannot override LLM model
- User cannot override `retention.*` settings
- Effective config is a complete dict with no missing keys

---

## File structure after Phase 1

```
app/
  state/
    workflow_state.py          ← WorkflowState, WorkflowStatus, WorkflowStep,
                                  StepExecution, HumanDecision, RunMetrics, WorkflowError
  schemas/
    job_score.py
    research_context.py
    resume_review.py
    review_audit.py
    career_advice.py
    interview_prep.py
    tailored_resume_draft.py
    fidelity_review.py
  repositories/
    database.py                ← connection, init, migration runner, purge_old_data
    workflow_repository.py
    job_repository.py
    resume_repository.py
    score_repository.py
    review_repository.py
    advice_repository.py
    tailoring_repository.py
    report_repository.py
    decision_repository.py
    config_repository.py
    step_repository.py         ← step_executions
    observability_repository.py
    security_repository.py
    memory_repository.py
  services/
    config_service.py

config/
  config.yaml          ← gitignored — never committed (contains your preferences)
  config.example.yaml  ← committed — template with v2 section, no sensitive values
tests/
  test_schemas.py
  test_repositories.py
  test_config_service.py
```

---

## Review Gate 1

Before any code is written, confirm:

**Workflow State**
- [ ] `WorkflowState` fields cover what every agent and workflow will need
- [ ] `WorkflowStatus` and `WorkflowStep` values are complete and stable
- [ ] `StepExecution` captures start, end, duration, and status for every step
- [ ] `step_history` is present in `WorkflowState` and is append-only
- [ ] `RunMetrics` includes `started_at` and `completed_at`
- [ ] `WorkflowError` includes `occurred_at` timestamp
- [ ] `HumanDecision` includes both `presented_at` and `decided_at`

**Agent Output Schemas**
- [ ] All 8 agent output schemas have the right fields for their agent's job
- [ ] The `resume_only_gaps` vs `career_gaps_observed` distinction is preserved in `ResumeReview`
- [ ] `TailoredBullet.supporting_evidence` is mandatory (no optional)
- [ ] `FidelityReview.approval_recommendation` drives the HITL gate for tailoring

**Database**
- [ ] All 18 DB tables are present and have the right columns
- [ ] All timestamp columns are `TEXT NOT NULL` in ISO 8601 UTC format
- [ ] `utcnow_iso()` utility exists in `database.py` and is the only way timestamps are produced
- [ ] `step_executions` table exists and includes `started_at`, `completed_at`, `duration_ms`
- [ ] `human_decisions` includes `presented_at` and `decided_at` (not just `created_at`)
- [ ] `run_metrics` includes `started_at` and `completed_at`
- [ ] All retention-critical tables have a `created_at` index
- [ ] `purge_old_data` utility is in `database.py`

**Configuration**
- [ ] `config.yaml` includes `retention.*` section with per-table windows
- [ ] `user_config` stores overrides only — not full config
- [ ] ConfigService enforces system limits even when user provides overrides
- [ ] Users cannot override LLM model, safety limits, or retention settings

**Approval to proceed to code:** Approved 2026-04-28 by Sivakumar Suthram
