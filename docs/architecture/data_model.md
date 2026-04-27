# Data Model – jobsearchagent-v2

---

## 1. Purpose

This document defines the **SQLite data model** for `jobsearchagent-v2`.

It describes:

* core tables
* relationships between entities
* how workflow state is persisted
* how agent outputs are stored
* how observability and security events are tracked
* how memory is structured

The goal is to ensure:

* consistency between workflows and storage
* traceability of decisions and outputs
* safe evolution of schemas over time

---

## 2. Design Philosophy

The data model follows these principles:

1. **Workflows are the source of truth**
   → everything ties back to `workflow_runs`

2. **Store structured outputs, not raw text blobs only**
   → use JSON columns with schema validation

3. **Separate snapshot vs history**
   → snapshot in `workflow_runs.state_json`
   → history in event tables

4. **Agents do not write directly to DB**
   → orchestrator persists validated outputs

5. **Prefer append-only for events**
   → observability and auditability

6. **SQLite first, schema evolution friendly**

---

## 3. Core Entity Relationships

```text
workflow_runs
   ├── jobs
   ├── resumes
   ├── job_scores
   ├── review_rounds
   ├── resume_reviews
   ├── career_advice
   ├── interview_prep
   ├── tailored_resumes
   ├── reports
   ├── human_decisions
   ├── agent_events
   ├── llm_calls
   ├── run_metrics
   ├── security_events
   └── memory_items
```

---

## 4. Core Tables

---

## 4.1 workflow_runs (central table)

### Purpose

Tracks each workflow execution and stores the current state snapshot.

---

### Schema

```sql
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
```

---

### Notes

* `state_json` stores serialized `WorkflowState`
* This is the authoritative snapshot of execution
* All workflows must be resumable from this state

---

## 4.2 jobs

### Purpose

Stores normalized job postings.

---

### Schema

```sql
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
```

---

### Notes

* `normalized_job_json` contains structured version used by agents
* raw job_description is retained but treated as untrusted input

---

## 4.3 resumes

### Purpose

Stores uploaded resumes and parsed profiles.

---

### Schema

```sql
CREATE TABLE resumes (
    id TEXT PRIMARY KEY,
    file_name TEXT,

    raw_text TEXT,
    parsed_profile_json TEXT,

    version INTEGER,
    is_active INTEGER,

    created_at TEXT NOT NULL
);
```

---

### Notes

* parsed profile is what agents should use
* raw_text should not be widely exposed to agents

---

## 4.4 job_scores

### Purpose

Stores scoring outputs per job.

---

### Schema

```sql
CREATE TABLE job_scores (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,

    score_json TEXT NOT NULL,
    overall_score INTEGER,

    created_at TEXT NOT NULL
);
```

---

### Notes

* `score_json` stores full structured output
* `overall_score` is indexed for sorting/filtering

---

## 4.5 review_rounds

### Purpose

Tracks reflection loop iterations.

---

### Schema

```sql
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
```

---

### Notes

* enables traceability of improvement across rounds
* critical for debugging reflection loop behavior

---

## 4.6 resume_reviews

### Purpose

Stores final resume critique outputs.

---

### Schema

```sql
CREATE TABLE resume_reviews (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,

    review_json TEXT NOT NULL,

    created_at TEXT NOT NULL
);
```

---

## 4.7 career_advice

### Purpose

Stores strategic guidance output.

---

### Schema

```sql
CREATE TABLE career_advice (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,

    advice_json TEXT NOT NULL,

    created_at TEXT NOT NULL
);
```

---

## 4.8 interview_prep

### Purpose

Stores interview preparation outputs.

---

### Schema

```sql
CREATE TABLE interview_prep (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,

    prep_json TEXT NOT NULL,

    created_at TEXT NOT NULL
);
```

---

## 4.9 tailored_resumes

### Purpose

Stores tailored resume drafts.

---

### Schema

```sql
CREATE TABLE tailored_resumes (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,

    tailored_json TEXT NOT NULL,

    approved INTEGER DEFAULT 0,

    created_at TEXT NOT NULL
);
```

---

## 4.10 reports

### Purpose

Stores final generated reports.

---

### Schema

```sql
CREATE TABLE reports (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,

    report_json TEXT,
    report_markdown TEXT,
    report_file_path TEXT,

    created_at TEXT NOT NULL
);
```

---

## 4.11 human_decisions

### Purpose

Tracks user decisions (HITL).

---

### Schema

```sql
CREATE TABLE human_decisions (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,

    decision_type TEXT,
    decision_value TEXT,

    payload_json TEXT,

    created_at TEXT NOT NULL
);
```

---

## 4.12 user_config

### Purpose

Stores user-specific configuration overrides for UI-adjustable preferences.
Stores only overrides, not full config
Merged with YAML defaults at runtime
Used by ConfigService

### Schema

```sql
CREATE TABLE user_config (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    config_key TEXT NOT NULL,
    config_value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```
---

## 5. Observability Tables

---

## 5.1 agent_events

```sql
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
```

---

## 5.2 llm_calls

```sql
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
```

---

## 5.3 run_metrics

```sql
CREATE TABLE run_metrics (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,

    total_llm_calls INTEGER,
    total_tokens_input INTEGER,
    total_tokens_output INTEGER,
    total_cost REAL,

    total_duration_ms INTEGER,

    created_at TEXT NOT NULL
);
```

---

## 6. Security Table

---

## 6.1 security_events

```sql
CREATE TABLE security_events (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,

    event_type TEXT,
    severity TEXT,

    description TEXT,

    created_at TEXT NOT NULL
);
```

---

### Example events

* prompt_injection_detected
* pii_redacted
* tool_access_blocked
* unsupported_claim_detected

---

## 7. Memory Table

---

## 7.1 memory_items

```sql
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

---

### Notes

* memory must be structured
* memory should not store raw LLM outputs
* memory must be selectively retrieved

---

## 8. Indexing Strategy

Recommended indexes:

```sql
CREATE INDEX idx_jobs_company ON jobs(company);
CREATE INDEX idx_jobs_title ON jobs(title);

CREATE INDEX idx_job_scores_score ON job_scores(overall_score);

CREATE INDEX idx_workflow_runs_status ON workflow_runs(status);

CREATE INDEX idx_review_rounds_run ON review_rounds(workflow_run_id);

CREATE INDEX idx_agent_events_run ON agent_events(workflow_run_id);
CREATE INDEX idx_llm_calls_run ON llm_calls(workflow_run_id);

CREATE INDEX idx_memory_type ON memory_items(memory_type);
```

---

## 9. JSON Storage Strategy

JSON fields are used extensively.

Examples:

```text
state_json
score_json
review_json
advice_json
prep_json
tailored_json
report_json
memory_value_json
```

---

### Rules

* Validate JSON before insert
* Avoid deeply nested structures
* Keep schemas versionable
* Do not store hidden chain-of-thought
* Store summaries instead of raw reasoning

---

## 10. Data Flow Summary

```text
Workflow starts → workflow_runs created
        ↓
Jobs fetched → jobs table
        ↓
Resume loaded → resumes table
        ↓
Scoring → job_scores
        ↓
Deep review → review_rounds + resume_reviews
        ↓
Career advice → career_advice
        ↓
Interview prep → interview_prep
        ↓
Tailoring → tailored_resumes
        ↓
Report → reports
        ↓
Events → agent_events + llm_calls
        ↓
Metrics → run_metrics
        ↓
Security → security_events
        ↓
Memory → memory_items
```

---

## 11. Schema Evolution Strategy

SQLite schema changes should be handled via:

* versioned migrations
* additive changes where possible
* avoiding destructive changes

Guidelines:

* add columns instead of modifying existing ones
* version JSON structures if needed
* keep backward compatibility for workflow state

---

## 12. Anti-Patterns to Avoid

Avoid:

* agents writing directly to database
* storing raw LLM outputs without structure
* storing entire prompts/responses unnecessarily
* mixing workflow snapshot with event history
* over-normalizing JSON-heavy data
* storing hidden reasoning chains
* uncontrolled memory growth

---

## 13. Final Principle

The data model should make it possible to answer:

```text
What happened?
Why did it happen?
What did the system decide?
What did the user decide?
What did the model produce?
How much did it cost?
Was it safe and correct?
```

If the data model cannot answer these questions, it is incomplete.
