# API Reference — Job Search Agent v2

**Base URL:** `http://localhost:8000`  
**Format:** JSON request and response bodies  
**Auth:** None (Phase 6 — local use only; Phase 7 adds API key header)

---

## Contents

- [Overview](#overview)
- [Common Patterns](#common-patterns)
- [Error Codes](#error-codes)
- [Endpoints](#endpoints)
  - [POST /workflows](#post-workflows)
  - [GET /workflows/{workflow_id}](#get-workflowsworkflow_id)
  - [POST /workflows/{workflow_id}/decisions](#post-workflowsworkflow_iddecisions)
  - [GET /workflows/{workflow_id}/jobs](#get-workflowsworkflow_idjobs)
  - [GET /workflows/{workflow_id}/report](#get-workflowsworkflow_idreport)
- [Schema Reference](#schema-reference)
  - [Request Bodies](#request-bodies)
  - [Response Bodies](#response-bodies)
  - [Decision Types](#decision-types)
  - [Status Values](#status-values)
  - [Error Object](#error-object)
- [Execution Limits](#execution-limits)
- [HITL Decision Flow](#hitl-decision-flow)

---

## Overview

The API exposes a single LangGraph workflow graph as a REST surface. The graph
runs in a background thread pool; callers poll `GET /workflows/{id}` to track
progress.

```
POST /workflows                 → start a run (202, async)
GET  /workflows/{id}            → poll status
POST /workflows/{id}/retry      → re-submit a workflow after a server restart (202)
POST /workflows/{id}/decisions  → submit a HITL decision (only used for tailoring approval)
GET  /workflows/{id}/jobs       → list scored jobs
GET  /workflows/{id}/report     → fetch the final report
GET  /config                    → effective merged config + protected key list
PUT  /config                    → upsert one user-config override (rejects protected keys)
```

> **Behaviour note.** The previous `select_jobs_for_deep_review` HITL pause has
> been removed. The graph now auto-selects up to `MAX_SELECTED_JOBS` (3) top
> scoring jobs where any track score (technical / architecture / leadership)
> meets `effective_config.scoring.min_match_score` (default 75). Workflows run
> end-to-end without any required user input. The `POST /decisions` endpoint
> still validates `select_jobs_for_deep_review` payloads for backwards-compat
> with older clients but the graph will no longer be in a state that accepts
> them — the call returns 409 `workflow_not_paused`.

---

## Common Patterns

### Asynchronous execution

`POST /workflows` and `POST /workflows/{id}/decisions` both return **202 Accepted**
immediately. The graph runs in a thread pool. Poll `GET /workflows/{id}` until
`status` is no longer `"running"`.

### Polling

```
while True:
    r = GET /workflows/{id}
    if r.status == "waiting_for_user":  # HITL — submit decision
    if r.status in ("completed", "failed"):  # done
    sleep(2)
```

### HITL sequence

1. Poll until `status == "waiting_for_user"`.
2. Read `pending_decision.decision_type` to know which decision to submit.
3. `POST /workflows/{id}/decisions` with the matching body.
4. Resume polling.

---

## Error Codes

All error responses share this structure:

```json
{
  "detail": {
    "error": "<error_code>",
    "message": "<human-readable explanation>",
    "workflow_id": "<id>"
  }
}
```

| HTTP | `error` code | Meaning |
|------|--------------|---------|
| 404 | `workflow_not_found` | No checkpoint exists for that `workflow_id` |
| 409 | `workflow_not_paused` | Decision submitted but graph has no active interrupt |
| 409 | `workflow_not_completed` | Report requested but workflow not yet `completed` |
| 422 | `decision_type_mismatch` | `decision_type` in body does not match the active interrupt |
| 422 | `invalid_job_ids` | One or more `selected_job_ids` are not in the eligible set |
| 422 | `too_many_jobs_selected` | More than `MAX_SELECTED_JOBS` (3) IDs submitted |
| 422 | _(Pydantic)_ | Request body fails schema validation |

---

## Endpoints

---

### POST /workflows

Start a new workflow run. Returns immediately with a `workflow_id`; execution
runs in the background.

**Request**

```
POST /workflows
Content-Type: application/json
```

```json
{
  "resume_id": "res-001",
  "search_criteria": {
    "roles": ["Staff Engineer"],
    "locations": ["Remote"],
    "keywords": ["distributed systems"]
  },
  "workflow_type": "full_career_review",
  "effective_config": {
    "scoring": {
      "career_track": "all",
      "min_match_score": 75
    }
  },
  "custom_urls": [
    "https://www.linkedin.com/jobs/view/4012345678",
    "https://acme.com/careers/staff-engineer"
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `resume_id` | string | yes | ID of the parsed resume to use |
| `search_criteria` | object | yes | Passed to `JobDiscoveryService.discover()` |
| `workflow_type` | string | no | Default: `"full_career_review"` |
| `effective_config` | object | no | Config overrides; merged with `config.yaml` defaults. Default: `{}`. Use `effective_config.scoring.min_match_score` (default 75) to set the per-run deep-review / interview-prep threshold (any track score ≥ this qualifies). |
| `custom_urls` | string[] | no | Up to 25 absolute URLs (LinkedIn, company career pages, ATS pages, etc.). Each is fetched and parsed via heuristics (JSON-LD, OpenGraph) → Claude (sonnet) fallback. Per-URL failures are logged in `errors[]` and do not abort the run. Default: `[]`. |

**Response — 202 Accepted**

```json
{
  "workflow_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "running",
  "created_at": "2026-05-01T12:00:00Z"
}
```

---

### GET /workflows/{workflow_id}

Poll the current status of a workflow run.

**Request**

```
GET /workflows/{workflow_id}
```

**Response — 200 OK**

```json
{
  "workflow_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "waiting_for_user",
  "current_step": "await_job_selection",
  "pending_decision": {
    "decision_type": "select_jobs_for_deep_review",
    "eligible_jobs": [
      {
        "job_id": "job-001",
        "title": "Staff Engineer",
        "company": "Acme Corp",
        "overall_score": 82,
        "match_summary": "Strong technical fit."
      }
    ]
  },
  "run_metrics": {
    "llm_calls": 4,
    "tokens_input": 12000,
    "tokens_output": 3200,
    "estimated_cost_usd": 0.045,
    "total_duration_ms": 8400,
    "started_at": "2026-05-01T12:00:00Z",
    "completed_at": null
  },
  "errors": [],
  "updated_at": "2026-05-01T12:00:08Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `workflow_id` | string | UUID of the run |
| `status` | string | See [Status Values](#status-values) |
| `current_step` | string \| null | Name of the last completed graph node |
| `pending_decision` | object \| null | Present only when `status == "waiting_for_user"`. Shape depends on `decision_type` — see [Decision Types](#decision-types) |
| `run_metrics` | object \| null | Cumulative token + cost counters |
| `errors` | array | Non-fatal per-job errors that did not abort the run |
| `updated_at` | string \| null | ISO-8601 timestamp of last state write |

**Response — 404** workflow not found.

---

### POST /workflows/{workflow_id}/decisions

Submit a human-in-the-loop decision to resume a paused workflow.

**Request**

```
POST /workflows/{workflow_id}/decisions
Content-Type: application/json
```

Body is a **discriminated union** on `decision_type`. See [Decision Types](#decision-types).

**Example — job selection**

```json
{
  "decision_type": "select_jobs_for_deep_review",
  "selected_job_ids": ["job-001", "job-003"]
}
```

**Example — tailoring approval**

```json
{
  "decision_type": "approve_tailoring",
  "approval": "approve"
}
```

**Response — 202 Accepted**

```json
{
  "workflow_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "running"
}
```

Graph resumes in background. Resume polling.

**Error responses**

| Status | Condition |
|--------|-----------|
| 404 | Workflow not found |
| 409 | Workflow exists but has no active interrupt (`workflow_not_paused`) |
| 422 | `decision_type` does not match the active interrupt |
| 422 | `selected_job_ids` contains IDs not in the eligible set |
| 422 | More than 3 jobs selected |
| 422 | Body fails Pydantic schema validation |

---

### GET /workflows/{workflow_id}/jobs

Return all scored jobs for a workflow. Available as soon as `score_jobs` completes
(before the workflow reaches `waiting_for_user`).

**Request**

```
GET /workflows/{workflow_id}/jobs
```

**Response — 200 OK**

```json
{
  "workflow_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "jobs": [
    {
      "job_id": "job-001",
      "title": "Staff Engineer",
      "company": "Acme Corp",
      "status": "scored",
      "overall_score": 82,
      "technical_score": 88,
      "architecture_score": 75,
      "leadership_score": 60,
      "domain_score": 70,
      "strengths": ["Python", "System design"],
      "gaps": ["Leadership scope"],
      "recommended_next_action": "Apply."
    }
  ]
}
```

Each item in `jobs` is a `JobSummaryResponse` — see [Response Bodies](#response-bodies).

**Response — 404** workflow not found.

---

### GET /workflows/{workflow_id}/report

Return the final report for a completed workflow. Returns **409** if the workflow
is still running or waiting for input.

**Request**

```
GET /workflows/{workflow_id}/report
```

**Response — 200 OK**

```json
{
  "workflow_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "report": {
    "markdown": "# Workflow Report\n\n## Summary\n...",
    "generated_at": "2026-05-01T12:05:30Z"
  }
}
```

**Response — 404** workflow not found.

**Response — 409** `workflow_not_completed` — workflow status is not `"completed"`.

---

### GET /config

Return the effective merged config (YAML defaults + DB user overrides) plus the
list of protected keys (read-only, enforced by `ConfigService._PROTECTED_KEYS`).

**Request**

```
GET /config
```

**Response — 200 OK**

```json
{
  "effective_config": {
    "search":   {"titles": ["Staff Engineer"], "locations": ["Remote"], "max_jobs": 10},
    "scoring":  {"min_match_score": 75},
    "salary":   {"min_desired": 130000, "currency": "USD"},
    "staleness": {"max_days": 14}
  },
  "protected_keys": [
    "limits.max_llm_calls_per_run",
    "limits.max_review_rounds",
    "llm.default_model",
    "llm.scoring_model",
    "scoring.deep_review_threshold"
  ]
}
```

---

### PUT /config

Set or update one user-config override. Repeated PUTs to the same `key` upsert
in place. Protected keys are rejected with **422 `protected_key`**.

**Request**

```
PUT /config
Content-Type: application/json
```

```json
{
  "key": "scoring.min_match_score",
  "value": 65
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | string | yes | Dotted config path (e.g. `search.max_jobs`, `scoring.min_match_score`). |
| `value` | any JSON | yes | New value — string, number, boolean, list, or object. |

**Response — 200 OK**

```json
{ "key": "scoring.min_match_score", "value": 65, "status": "saved" }
```

**Response — 422** `protected_key` — the key is in `_PROTECTED_KEYS` and cannot be overridden via the API.

**Per-agent provider / model overrides (ADR-053)**

The `agents.*` keys let you reroute a specific agent to a different
provider+model. Both `provider` and `model` are validated against the
`ModelRegistry` — unknown values return 422.

| Key | Example value |
|-----|---------------|
| `agents.research_agent.provider`    | `claude` \| `openai` |
| `agents.research_agent.model`       | `claude-haiku-4-5-20251001`, `gpt-4o-mini`, ... |
| `agents.scoring_agent.provider`     | (same options) |
| `agents.scoring_agent.model`        | (same options) |
| `agents.resume_critic.provider`     | (same options) |
| `agents.resume_critic.model`        | (same options) |
| `agents.career_advisor.provider`    | (same options) |
| `agents.career_advisor.model`       | (same options) |
| `agents.interview_coach.provider`   | (same options) |
| `agents.interview_coach.model`      | (same options) |
| `agents.tailoring_agent.provider`   | (same options) |
| `agents.tailoring_agent.model`      | (same options) |
| `agents.review_auditor.provider`    | (same options) |
| `agents.review_auditor.model`       | (same options) |
| `agents.fidelity_reviewer.provider` | (same options) |
| `agents.fidelity_reviewer.model`    | (same options) |

> **Restart-to-apply.** Saved per-agent overrides take effect after the
> backend is restarted. Workflows in flight continue under the assignment
> they were built with.

---

### GET /config/providers

Return the `ModelRegistry`'s known providers and models, with indicative
per-million-token cost. Used by the UI to populate the per-agent dropdowns.

**Request**

```
GET /config/providers
```

**Response — 200 OK**

```json
{
  "providers": {
    "claude": {
      "available": true,
      "models": [
        {"id": "claude-haiku-4-5-20251001",  "input_per_m": 0.25, "output_per_m": 1.25},
        {"id": "claude-sonnet-4-6",          "input_per_m": 3.00, "output_per_m": 15.00},
        {"id": "claude-opus-4-7",            "input_per_m": 15.0, "output_per_m": 75.00}
      ]
    },
    "openai": {
      "available": true,
      "models": [
        {"id": "gpt-4o-mini", "input_per_m": 0.15, "output_per_m": 0.60},
        {"id": "gpt-4o",      "input_per_m": 2.50, "output_per_m": 10.00},
        {"id": "o1",          "input_per_m": 15.0, "output_per_m": 60.00}
      ]
    }
  },
  "agent_assignment": {
    "research_agent":    {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    "scoring_agent":     {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    "resume_critic":     {"provider": "claude", "model": "claude-sonnet-4-6"},
    "review_auditor":    {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    "career_advisor":    {"provider": "claude", "model": "claude-sonnet-4-6"},
    "interview_coach":   {"provider": "claude", "model": "claude-sonnet-4-6"},
    "tailoring_agent":   {"provider": "claude", "model": "claude-sonnet-4-6"},
    "fidelity_reviewer": {"provider": "claude", "model": "claude-haiku-4-5-20251001"}
  }
}
```

`available: false` indicates the provider's API key isn't set on the server
(e.g. missing `OPENAI_API_KEY`). The UI should disable that provider's
options and show the reason.

---

## Schema Reference

### Request Bodies

#### StartWorkflowRequest

```
resume_id        string   required   Parsed resume ID
search_criteria  object   required   Passed to discovery service
workflow_type    string   optional   Default: "full_career_review"
effective_config object   optional   Config key overrides. Default: {}
```

**`search_criteria` shape (informal)**

```json
{
  "roles": ["Staff Engineer", "Principal Engineer"],
  "locations": ["Remote", "New York"],
  "keywords": ["distributed systems", "platform"]
}
```

All fields are passed through to `JobDiscoveryService.discover()` — shape is
determined by the discovery implementation.

**`effective_config` shape (examples)**

```json
{
  "scoring": {
    "career_track": "all"
  }
}
```

Valid `career_track` values: `"ic"` | `"architect"` | `"management"` | `"all"` (default — weights all three tracks equally)

---

#### JobSelectionDecision

```
decision_type    "select_jobs_for_deep_review"   (literal, required)
selected_job_ids array[string]                   min 1, max 3 items
```

`selected_job_ids` must be a subset of the `eligible_jobs[].job_id` values
from `pending_decision` in the status response.

---

#### TailoringDecision

```
decision_type    "approve_tailoring"          (literal, required)
approval         "approve" | "revise" | "reject"   (required)
```

| Value | Effect |
|-------|--------|
| `"approve"` | Accept the tailored resume draft; workflow proceeds to report |
| `"revise"` | Request another tailoring pass (within `MAX_REVIEW_ROUNDS`) |
| `"reject"` | Discard the tailored draft; workflow proceeds to report without it |

---

### Response Bodies

#### WorkflowStatusResponse

```
workflow_id       string
status            string            See Status Values
current_step      string | null     Last completed graph node name
pending_decision  object | null     Present only when waiting_for_user
run_metrics       object | null     See RunMetrics
errors            array[ErrorEntry] Non-fatal per-job errors
updated_at        string | null     ISO-8601
```

**`pending_decision` when `decision_type == "select_jobs_for_deep_review"`**

```json
{
  "decision_type": "select_jobs_for_deep_review",
  "eligible_jobs": [
    {
      "job_id": "job-001",
      "title": "Staff Engineer",
      "company": "Acme Corp",
      "overall_score": 82,
      "match_summary": "Strong technical fit."
    }
  ]
}
```

**`pending_decision` when `decision_type == "approve_tailoring"`**

```json
{
  "decision_type": "approve_tailoring",
  "job_id": "job-001",
  "fidelity_risk_summary": "Low risk. All claims are supported."
}
```

---

#### JobSummaryResponse

```
job_id                    string
title                     string
company                   string
status                    string         Job lifecycle status (e.g. "scored", "shortlisted")
overall_score             int | null     0–100
technical_score           int | null     0–100
architecture_score        int | null     0–100
leadership_score          int | null     0–100
domain_score              int | null     0–100
strengths                 array[string]
gaps                      array[string]
recommended_next_action   string | null
```

---

#### ReportResponse

```
workflow_id   string
report        object   {markdown: string, generated_at: string (ISO-8601)}
```

---

#### RunMetrics

```
llm_calls             int
tokens_input          int
tokens_output         int
estimated_cost_usd    float
total_duration_ms     int
started_at            string   ISO-8601
completed_at          string | null
```

---

### Decision Types

The `POST /workflows/{id}/decisions` body is a **discriminated union** on `decision_type`.
The active `decision_type` is always available in `pending_decision.decision_type` from
the status endpoint.

| `decision_type` | Body schema | Graph node that raised it |
|-----------------|-------------|--------------------------|
| `select_jobs_for_deep_review` | `JobSelectionDecision` | `await_job_selection` |
| `approve_tailoring` | `TailoringDecision` | `await_tailoring_approval` |

Submitting a `decision_type` that does not match the active interrupt returns
`422 decision_type_mismatch`.

---

### Status Values

#### Workflow status (`WorkflowStatusResponse.status`)

| Value | Meaning |
|-------|---------|
| `running` | Graph is executing in the background thread pool |
| `waiting_for_user` | Graph has hit a `interrupt()` — `pending_decision` is populated |
| `completed` | All nodes finished; report is available |
| `failed` | Unrecoverable error; check `errors` array |

#### Job status (`JobSummaryResponse.status`)

| Value | Meaning |
|-------|---------|
| `discovered` | Job found by discovery service, not yet scored |
| `scored` | Scoring agent has run |
| `shortlisted` | Score ≥ threshold; selected for deep review |
| `reviewed` | Resume Critic + Review Auditor completed |
| `applied` | User marked as applied |
| `passed` | Removed from active tracking |
| `rejected` | Application rejected |
| `offer` | Offer received |
| `error` | Agent error during processing |
| `skipped` | Skipped due to budget exhaustion |

---

### Error Object

Entries in `WorkflowStatusResponse.errors`:

```
step              string    Graph node where the error occurred
error_type        string    Category (e.g. "LLMProviderError", "ScoringError")
message           string    Human-readable description
recoverable       bool      Whether the run continued after this error
occurred_at       string    ISO-8601
suggested_action  string | null
```

These are **non-fatal** per-job errors. The run continues; the affected job is
marked `status = "error"` and excluded from downstream steps. Fatal errors that
abort the entire run set `workflow.status = "failed"`.

---

## Execution Limits

These limits are enforced by the orchestrator and cannot be overridden at runtime.

| Constant | Value | Enforced where |
|----------|-------|----------------|
| `MAX_JOBS_PER_RUN` | 10 | `discover_jobs` node — excess jobs are silently dropped |
| `MAX_SELECTED_JOBS` | 3 | Decision endpoint (422) + `JobSelectionDecision` schema |
| `MAX_RESEARCH_STEPS` | 2 | ResearchAgent ReAct loop |
| `MAX_REVIEW_ROUNDS` | 3 | `deep_review` reflection loop |
| `MAX_LLM_CALLS_PER_JOB` | 10 | Per-job budget check in scoring loop |
| `MAX_LLM_CALLS_PER_RUN` | 100 | `check_budget()` called before every agent invocation |

When `MAX_LLM_CALLS_PER_RUN` is hit, remaining unscored jobs are marked `"skipped"`
and the workflow proceeds to `await_job_selection` with whatever jobs are already scored.

---

## HITL Decision Flow

```
POST /workflows
        │
        ▼ (background)
   discover_jobs
        │
   load_resume
        │
   score_jobs  ──── budget exhausted ──▶ (skip remaining)
        │
   await_job_selection  ◀─── status: waiting_for_user
        │
   POST /workflows/{id}/decisions  (decision_type: select_jobs_for_deep_review)
        │
        ▼ (background)
   deep_review  (per selected job)
        │
   career_advice
        │
   interview_prep?  (score ≥ 75 or user requested)
        │
   tailoring_check
        │
   await_tailoring_approval?  ◀─── status: waiting_for_user (if user requested)
        │
   POST /workflows/{id}/decisions  (decision_type: approve_tailoring)
        │
        ▼ (background)
   fidelity_review
        │
   generate_report
        │
   status: completed
        │
   GET /workflows/{id}/report
```

Workflows that do not trigger tailoring skip `await_tailoring_approval` and
proceed directly to `generate_report`.
