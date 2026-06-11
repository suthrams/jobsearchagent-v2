# API Reference — Job Search Agent v2

**Base URL:** `http://localhost:8000`  
**Format:** JSON request and response bodies  
**Auth:** None (Phase 6 — local use only; Phase 7 adds API key header)

---

## Contents

- [Overview](#overview)
- [Common Patterns](#common-patterns)
- [Error Codes](#error-codes)
- [Health and readiness](#health-and-readiness-adr-084)
- [Endpoints](#endpoints)
  - [POST /workflows](#post-workflows)
  - [GET /workflows/{workflow_id}](#get-workflowsworkflow_id)
  - [GET /workflows/{workflow_id}/jobs](#get-workflowsworkflow_idjobs)
  - [GET /workflows/{workflow_id}/report](#get-workflowsworkflow_idreport)
- [On-Demand Tailoring](#on-demand-tailoring)
  - [POST /workflows/{workflow_id}/jobs/{job_id}/tailorings](#post-workflowsworkflow_idjobsjob_idtailorings)
  - [GET /workflows/{workflow_id}/tailorings](#get-workflowsworkflow_idtailorings)
  - [GET /tailorings/{tailoring_id}](#get-tailoringstailoring_id)
  - [POST /tailorings/{tailoring_id}/decisions](#post-tailoringstailoring_iddecisions)
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

> 🗺  **Visual map**: see [`api_surface_overview.md`](api_surface_overview.md)
> for a one-page diagram of every endpoint grouped by domain, plus the two
> typical user journeys (job-search run + Resume Clinic chat-edit loop).
> The detailed per-endpoint contract lives in this file.

The API exposes a single LangGraph workflow graph as a REST surface. The graph
runs in a background thread pool; callers poll `GET /workflows/{id}` to track
progress.

```
POST /workflows                                → start a run (202, async; optional Idempotency-Key, ADR-082)
GET  /workflows/{id}                           → poll status
POST /workflows/{id}/retry                     → re-submit a workflow after a server restart (202)
POST /workflows/{id}/cancel                    → request cooperative cancellation of a running run (202, ADR-083)
POST /workflows/{id}/scoring                   → ADR-060 phase 2: score selected jobs from a manual-selection run (202, async)
GET  /workflows/{id}/jobs                      → list scored jobs
GET  /workflows/{id}/report                    → fetch the final report
POST /workflows/{wf}/jobs/{job}/tailorings     → create a tailoring draft for ANY scored job; deep-reviews on demand first if needed (ADR-061; run tailoring + fidelity, 200)
POST /workflows/{wf}/jobs/{job}/deep-review    → run the critic+auditor loop for one scored job on demand (ADR-061, 200)
POST /workflows/{wf}/jobs/{job}/interview-prep → run the interview coach for one scored job on demand (ADR-061, 200)
POST /workflows/{wf}/jobs/{job}/score          → research + score ONE previously-unscored job on demand; it then joins the regular route (ADR-100 Phase 2). Idempotent (already-scored returns the existing score); 409 resume_profile_missing; 502 scoring_failed
GET  /workflows/{wf}/tailorings                → list tailoring drafts for a workflow
GET  /tailorings/{id}                          → fetch a single tailoring draft (top-level: ID is globally unique)
POST /tailorings/{id}/decisions                → record approve / revise / reject / edit for a draft
GET  /config                                   → effective merged config + protected key list
PUT  /config                                   → upsert one user-config override (rejects protected keys)
GET  /users                                    → list profiles (ADR-062; default user 0 first)
POST /users                                    → create a profile, returns its assigned id (201)
PUT  /users/{id}                               → update a profile's name / note (200)
POST /users/{id}/resume                        → upload + parse a PDF resume for a profile (ADR-062, 201)
DELETE /users/{id}/resume/{resume_id}          → delete a resume; cascades to its clinic reviews (200)
POST /users/{id}/resume-clinic                 → run a Resume Clinic review on a resume (ADR-066, 200)
GET  /users/{id}/resume-clinic                 → list past clinic runs for a profile
POST /resume-clinic/{id}/decisions             → record approve / revise / reject / edit (runs the fidelity gate on approve/edit, ADR-092)
POST /resume-clinic/{id}/chat                  → one chat-revise turn (ADR-068; no per-turn fidelity since ADR-092)
POST /resume-clinic/{id}/fidelity-check        → run the Fidelity Reviewer on the current draft on demand (ADR-092, 200)
POST /resume-clinic/{id}/discard-edits         → revert chat edits to the agent overhaul (ADR-068, 200)
GET  /resume-clinic/{id}/export                → render the clinic resume in md/txt/html/json/docx/pdf (200)
GET  /users/{id}/favorites                     → list My favorite jobs for a profile (ADR-090)
POST /users/{id}/favorites                     → favorite a job {workflow_id, job_id}; 201; 409 favorites_cap_reached; 404 job_not_found
DELETE /users/{id}/favorites/{job_id}          → un-favorite a job (idempotent, 204)
GET  /users/{id}/review-later                  → list the Maybe/Review-later jobs for a profile (ADR-100)
POST /users/{id}/review-later                  → move a job to review-later {workflow_id, job_id}; 201; 409 review_later_cap_reached; 404 job_not_found
DELETE /users/{id}/review-later/{job_id}       → remove from review-later (idempotent, 204)
POST /admin/purge                              → run the data-retention purge (ADR-070; contract, impl pending, 200)
```

**Identity (ADR-062).** Every endpoint resolves the acting profile through a
single dependency, `get_current_user_id`, which reads an optional `?user_id=`
**query parameter** (no HTTP headers). Absent → falls back to `"0"`, the
pre-existing-data profile (backward compatible). The id is validated against the
`users` table. Reads (config, history) and writes (config overrides, run owner)
are scoped to the resolved id. Isolation is cooperative, not enforced — see
`security.model.md` and ADR-062 Decision E.

**URL convention notes.** Tailorings use a workflow-scoped path for create + list (a tailoring is created in the context of a workflow + job) and a top-level path for fetch + decision (the `tailoring_id` is a globally unique UUID, so once you have it, the workflow scope is redundant — same pattern as GitHub's `/repos/.../issues` for list vs `/issues/{id}` for fetch). `POST /workflows/{id}/retry` is an action verb, not a resource — accepted as a documented exception because the operation has no clean resource form.

> **Behaviour note.** The workflow graph runs end-to-end with no `interrupt()`
> pauses (ADR-059). Job selection auto-selects up to `MAX_SELECTED_JOBS` top
> scoring jobs where any **active** track score (the profile's `scoring.tracks`
> subset of technical / architecture / leadership, ADR-071)
> meets `effective_config.scoring.min_match_score` (default 75). The only HITL
> in the system is the out-of-graph tailoring decision
> (`POST /tailorings/{id}/decisions`). The former in-graph
> `POST /workflows/{id}/decisions` endpoint was removed in ADR-059.
>
> **Manual scoring selection (ADR-060, opt-in).** When
> `effective_config.scoring.manual_selection` is true, a run discovers a wider
> net and parks at status `awaiting_scoring_selection` without scoring.
> `POST /workflows/{id}/scoring` with `{"selected_job_ids": [...]}` then scores
> only the chosen jobs (re-entering the same graph/thread at `score_jobs`).
> Valid only while `awaiting_scoring_selection` (else `409`); ids that were not
> discovered for the run yield `422`. This is curate-before-scoring and adds no
> `interrupt()` — the human choice sits between two phases of one `workflow_id`.

---

## Common Patterns

### Asynchronous execution

`POST /workflows` returns **202 Accepted** immediately. The graph runs in a
thread pool. Poll `GET /workflows/{id}` until `status` is no longer `"running"`.

### Polling

```
while True:
    r = GET /workflows/{id}
    if r.status in ("completed", "failed"):  # done
    sleep(2)
```

### Tailoring + decision (out-of-graph HITL)

1. After a run completes, `POST /workflows/{wf}/jobs/{job}/tailorings` to create a draft.
2. Review the draft and its fidelity flags.
3. `POST /tailorings/{id}/decisions` with `approval` in `{approve, revise, reject, edit}` (`edit` also sends the human-authored draft).

### Idempotent kickoff (ADR-082)

`POST /workflows` accepts an optional `Idempotency-Key` request header. A run is a
real bill (LLM spend), so a retried kickoff must not start a second one. With the
header set:

- same key + same request body -> the original `202` response is replayed; **no
  second run is started**.
- same key + a different body -> `409 idempotency_key_reused`.
- absent the header -> behaviour is unchanged (each call is its own run).

The re-entry endpoints (`POST /workflows/{id}/retry`, `POST /workflows/{id}/scoring`)
do not take a key; their natural dedup key is the `workflow_id`, protected by an
in-flight execution guard that returns `409 workflow_already_running` if the run is
already executing.

### Cancellation (ADR-083)

`POST /workflows/{id}/cancel` requests cooperative cancellation of a running run.
It returns `202 {workflow_id, status: "cancelling"}`; the run stops at the **next
node boundary** (a node already executing finishes first) and is then finalized to
`cancelled`. Idempotent (re-cancel returns `202`). `409 workflow_not_cancellable`
if the run has no pending steps (already terminal or parked). The Streamlit Live
Run Monitor exposes a Cancel control while a run is running / cancelling.

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
| 409 | `workflow_not_completed` | Report requested but workflow not yet `completed` |
| 404 | `tailoring_not_found` | No tailoring draft exists for that `tailoring_id` |
| 409 | `idempotency_key_reused` | An `Idempotency-Key` was reused with a different request body (ADR-082) |
| 409 | `workflow_already_running` | A retry / scoring re-submit was refused because the run is already executing (ADR-082) |
| 409 | `workflow_not_cancellable` | Cancel requested on a run with no pending steps (already terminal or parked) (ADR-083) |
| 422 | `validation_error` | Request body / path / query fails schema validation. Pydantic field errors appear in `detail.details` (a list). Normalised by a global handler in `app/api/main.py` so the consumer reads errors uniformly across all endpoints. |

---

## Health and readiness (ADR-084)

Two unauthenticated infrastructure endpoints. They do **not** take the `?user_id=`
seam, and they are **excluded** from `api_requests` recording (probes would flood
the table and a `503` from `/readyz` must not skew the dashboard's API error rate).

### GET /health

Liveness. No dependency I/O; returns `200` whenever the process is serving.

```json
{ "status": "ok", "service": "jobsearchagent-v2", "version": "2.0.0" }
```

### GET /readyz

Readiness. Probes the shared dependencies (not the individual routes) and aggregates.
**Secret-safe**: reports presence/mode only, never key values.

Checks: `database` (critical), `agent_provider` (Anthropic `live`/`mock`), `adzuna`
(configured?), `openai` (optional). Aggregate `status`:

| status | HTTP | When |
|---|---|---|
| `ready` | 200 | all critical + capability checks green |
| `degraded` | 200 | DB ok but a capability is unavailable (mock mode, or Adzuna unconfigured) |
| `down` | 503 | the `database` check failed (the one critical dependency) |

**Response (200, ready):**

```json
{
  "status": "ready",
  "checks": {
    "database":       { "ok": true, "detail": "SELECT 1 ok", "latency_ms": 2 },
    "agent_provider": { "ok": true, "mode": "live", "detail": "live" },
    "adzuna":         { "ok": true, "detail": "configured" },
    "openai":         { "ok": true, "detail": "configured", "optional": true }
  },
  "checked_at": "2026-06-06T12:00:00Z"
}
```

A `degraded` body has the same shape with `status: "degraded"` and the offending
checks `ok: false` (still HTTP 200). A `down` body returns HTTP `503`.

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
| `effective_config` | object | no | Config overrides; merged with `config.yaml` defaults. Default: `{}`. Use `effective_config.scoring.min_match_score` (default 75) to set the per-run deep-review / interview-prep threshold (any **active** track score ≥ this qualifies). `effective_config.scoring.tracks` (ADR-071) sets the profile's active-track subset; inactive tracks are scored `null`. |
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
  "status": "running",
  "current_step": "score_jobs",
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
| `run_metrics` | object \| null | Cumulative token + cost counters |
| `errors` | array | Non-fatal per-job errors that did not abort the run |
| `updated_at` | string \| null | ISO-8601 timestamp of last state write |

**Response — 404** workflow not found.

---

### GET /workflows/{workflow_id}/jobs

Return all scored jobs for a workflow. Available as soon as `score_jobs` completes.

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

## On-Demand Tailoring

Out-of-graph tailoring (ADR-055). The same `TailoringAgent` and `FidelityReviewer` that the workflow node would use, exposed as a small REST surface so tailoring can be triggered per job after the workflow completes — the typical case, since users decide which jobs deserve a tailored draft after seeing scoring + deep-review output.

The flow:

![On-demand tailoring flow, left to right: a completed workflow with selected_jobs in the checkpoint; the user triggers POST workflows jobs tailorings (synchronous, about 5 to 15 seconds), which runs the Tailoring Agent then the Fidelity Reviewer to produce a tailored_resumes draft returning tailoring_id, draft and fidelity_review; the human reviews diffs and evidence; POST tailorings decisions records approve, revise, reject or edit; the decision persists, approved when approve or edit. Out-of-graph with no interrupt; the human owns the decision.](images/api_ondemand_tailoring.png)

<sub>Figure source: `tools/figure_renderer/specs/api_ondemand_tailoring.json` (deterministic renderer; text is verbatim from the spec).</sub>

### POST /workflows/{workflow_id}/jobs/{job_id}/tailorings

Run the Tailoring Agent and the Fidelity Reviewer for one job. Synchronous — typically 5–15 s wall clock (~6 LLM calls). The resulting draft is persisted to `tailored_resumes`. Repeated calls for the same `(workflow_id, job_id)` produce additional rows; the caller decides whether to use the latest.

ADR-061: tailoring is available for **any scored job**, not only the auto-selected top-3. If the job has no deep-review row yet and `auto_deep_review` is true (the default), the critic+auditor reflection loop runs for that one job first (adding ~20–40 s and a critic pass), so the Tailoring Agent gets real review context.

**Request**

```
POST /workflows/{workflow_id}/jobs/{job_id}/tailorings?auto_deep_review=true
```

Optional query param `auto_deep_review` (default `true`). No body. The router pulls everything it needs from the workflow's checkpoint (`resume_profile`, `scored_jobs`) and the relational repos (`final_resume_review`, `career_advice`).

### POST /workflows/{workflow_id}/jobs/{job_id}/deep-review

ADR-061. Run the ResumeCritic + ReviewAuditor reflection loop for one scored job on demand, out-of-graph (same shared loop the `deep_review` node uses). Persists rounds + the final review via `ReviewRepository`. Returns `{workflow_id, job_id, review, rounds, llm_calls, errors}`. 409 if `resume_profile` is missing; 502 if the loop fails.

### POST /workflows/{workflow_id}/jobs/{job_id}/score

ADR-100 Phase 2. Research + score ONE previously-unscored job on demand, out-of-graph, via the shared `scoring_runner.score_one_job` (the same logic the `score_jobs` node fans out). Lets a user send a job from the Review-later list — or any discovered-but-unscored job — through the normal research+scoring path; once scored it surfaces in Matches and is eligible for every on-demand op (deep-review, tailoring, interview-prep). NOT bounded by `scoring.max_scored` (an explicit single-job op). **Idempotent**: a job already scored for the run returns its existing score with no re-spend. Returns `{workflow_id, job_id, already_scored, overall_score, llm_calls?}`. 409 if `resume_profile` is missing; 502 if scoring fails.

### POST /workflows/{workflow_id}/jobs/{job_id}/interview-prep

ADR-061. Run the InterviewCoach for one chosen scored job on demand, out-of-graph, regardless of threshold. Career-advice and final-review context are sourced from the repos. Persists via `AdviceRepository.create_prep`. Returns `{workflow_id, job_id, prep}`. 409 if `resume_profile` is missing; 502 if the coach fails.

**Response — 200 OK**

```json
{
  "tailoring_id": "uuid",
  "workflow_id": "uuid",
  "job_id": "string",
  "resume_id": "string",
  "tailored": {
    "headline_suggestions": [
      {
        "original_text": "...",
        "suggested_text": "...",
        "supporting_evidence": "Resume line: '...'",
        "claim_type": "reword | emphasize | gap | remove",
        "fidelity_risk": "low | medium | high",
        "section_label": "headline",
        "impact_rationale": "Sentence (<=25w) explaining JD fit; never generic praise.",
        "unsupported_claims": []
      }
    ],
    "summary_suggestions": [
      {
        "original_text": "...",
        "suggested_text": "...",
        "supporting_evidence": "Resume line: '...'",
        "claim_type": "reword | emphasize | gap | remove",
        "fidelity_risk": "low | medium | high",
        "section_label": "summary",
        "impact_rationale": "...",
        "unsupported_claims": []
      }
    ],
    "experience_bullet_suggestions": [
      {
        "original_text": "...",
        "suggested_text": "...",
        "supporting_evidence": "Resume line: '...'",
        "claim_type": "reword | emphasize | gap | remove",
        "fidelity_risk": "low | medium | high",
        "section_label": "experience:Acme:Staff Engineer",
        "impact_rationale": "...",
        "unsupported_claims": []
      }
    ],
    "skills_section_suggestions": ["..."],
    "overall_tailoring_notes": "Strategy summary, 3-5 sentences <=120w. Sentence 1 is a positioning thesis.",
    "fidelity_risk_summary": "..."
  },
  "fidelity_review": {
    "overall_fidelity_status": "pass | needs_revision | fail",
    "approval_recommendation": "approve | revise | reject",
    "unsupported_claims": [...],
    "fabricated_metrics": [...],
    "inflated_scope_flags": [...],
    "unsupported_technology_flags": [...],
    "unsupported_certification_flags": [...],
    "required_removals": [...],
    "required_revisions": [...],
    "confidence": 92
  },
  "decision": null,
  "approved": false,
  "decided_at": null,
  "created_at": "ISO-8601"
}
```

**Response — 404** `workflow_not_found` or `job_not_found`.

**Response — 409** `resume_profile_missing` — workflow has not yet reached `load_resume`.

**Response — 502** `tailoring_failed` — the Tailoring Agent raised an `LLMProviderError` after retries.

If the Fidelity Reviewer fails (rare), the draft is still persisted with `fidelity_review: null` so the user can see it; the UI surfaces this case explicitly.

**Schema notes (ADR-056).** `claim_type="remove"` is the only way to free page space (carries empty `suggested_text` and the bullet to delete in `supporting_evidence`); `"gap"` is the only way to surface missing experience (also empty `suggested_text`). `section_label` lets the UI group suggestions in resume order — values are `"headline"`, `"summary"`, `"experience:<company>:<title>"`, `"skills"`, `"education:<institution>"`, `"certifications:<name>"`. `impact_rationale` is the per-suggestion "why for this role" string and must reference a concrete JD signal — generic phrasing praise is rejected by the Fidelity Reviewer with a `required_revisions` flag. The page-budget contract requires `suggested_text` word count to fall in `ceil(0.85 * original_words) .. floor(1.05 * original_words)` for non-headline sections; headline relaxes to "match within +/- 3 words". `overall_tailoring_notes` is now the draft's strategy summary (positioning thesis + concrete JD-anchored moves; <=120 words).

---

### GET /workflows/{workflow_id}/tailorings

List all tailoring drafts for a workflow, newest first. Returns an empty list when no drafts exist (never 404).

**Response — 200 OK**

```json
{
  "workflow_id": "uuid",
  "tailorings": [
    { "tailoring_id": "uuid", "job_id": "string", "decision": "approve", ... },
    ...
  ]
}
```

---

### GET /tailorings/{tailoring_id}

Fetch a single draft by ID. Same shape as the trigger endpoint response.

**Response — 404** `tailoring_not_found`.

---

### POST /tailorings/{tailoring_id}/decisions

Record the user's approve / revise / reject / edit choice. Idempotent: re-submitting overwrites the previous decision and updates `decided_at`. Updates `tailored_resumes.decision`, `decided_at`, the `approved` flag (1 when `approval` is `"approve"` or `"edit"`), and `edited_json` (the human-authored draft) on an edit.

**Request**

```json
{
  "approval": "approve | revise | reject | edit",
  "edited": { "...": "human-authored draft; required only when approval == edit" }
}
```

`edit` means the user accepted the draft with their own wording. The `edited`
draft is stored as authored by the user and is **not** re-run through the
Fidelity Reviewer (ADR-059) — the reviewer polices the agent, not the
accountable human. The agent's original draft is retained in `tailored_json`.

**Response — 200 OK** — the updated draft (same shape as the trigger response), including `edited` when present.

**Response — 404** `tailoring_not_found`.

### POST /tailorings/{tailoring_id}/chat-session

ADR-072. Open (create-or-reuse) a **live-chat session** seeded from a job's
tailored draft, so the user can refine the resume inline and export it using the
Resume Clinic chat + export stack. Create-or-reuse: a second call for the same
(originating run, job) returns the existing session, so reopening preserves chat
edits. The seed is the clicked draft's human edit (`edited_json`) if present, else
the agent draft (`tailored_json`), converted to a clinic overhaul by the
deterministic `tailored_draft_to_overhaul` (reword/emphasize seeded; gap and
remove dropped — per-bullet removal is not honored by the renderer). No LLM call.

The session is a `resume_clinic_reviews` row tagged with `source_workflow_run_id`
(the job-search run) + `job_id`, so it lists under the job and is excluded from the
clinic past-runs panel. Chat turns and export then use the existing
`POST /resume-clinic/{id}/chat` and `GET /resume-clinic/{id}/export` on the
returned `clinic_id` (fidelity runs every turn; the 25-turn cost cap applies).

**Response — 200 OK** — the clinic-session row (`ResumeClinicResponse`: `clinic_id`,
`resume_id`, `overhaul`, ...).

**Response — 404** `tailoring_not_found` / `resume_not_found`.

**Response — 422** Pydantic validation — `approval` must be one of the four literals; `edited` is required when `approval == "edit"`.

---

## Resume Clinic (ADR-066)

A standalone, job-agnostic resume review. Runs on the resume alone — no
discovery, no scoring, no LangGraph. Out-of-graph, same pattern as on-demand
tailoring (ADR-055). The clinic produces a quality scorecard, an optional
role/track alignment, and an evidence-bound overhaul (reorganization plan +
rewrites). The Fidelity Reviewer always runs on the rewrites; a human `edit`
decision is owner-authored and is NOT re-reviewed (ADR-059).

The path-based `{user_id}` declares which profile the operation is for. This
endpoint family does NOT consult the `?user_id=` query-param seam (a FastAPI
collision between the path param and the seam's query param). Cooperative
scoping per ADR-062 — the path is not an authentication boundary.

---

### POST /users/{user_id}/resume-clinic

Run a clinic review end-to-end. Returns the persisted row.

**Request body** (all fields optional)

```json
{
  "resume_id": "...",            // defaults to the user's active resume
  "target_role": "...",          // free text; absent -> quality-only mode
  "target_track": "ic | architect | management",
  "seniority_aware": false
}
```

**Response — 200 OK**

```json
{
  "clinic_id": "...",
  "user_id": "0",
  "resume_id": "...",
  "workflow_run_id": "...",
  "target_role": "...",
  "target_track": "ic",
  "seniority_aware": true,
  "quality":   { "dimensions": [...], "overall_summary": "..." },
  "alignment": { "fit_summary": "...", "missing_skills": [...], "confidence": "medium" },
  "overhaul":  { "reorganization": {...}, "rewrites": [...] },
  "fidelity_review": { "approval_recommendation": "approve", "confidence": 90, ... },
  "decision": null,
  "edited": null,
  "decided_at": null,
  "created_at": "2026-05-28T..."
}
```

`alignment` is null when no `target_role` and no `target_track` is given.
`fidelity_review` is null when there were no rewrites or fidelity raised an
`LLMProviderError`.

**Response — 404** `resume_not_found` — when `resume_id` is unknown, when the
resume is owned by a different profile, or when the user has no active resume
and `resume_id` was omitted.

**Response — 422** invalid `target_track` (must be one of `ic`, `architect`,
`management`).

**Response — 502** `clinic_failed` — reviewer LLM error.

---

### GET /users/{user_id}/resume-clinic

List past clinic runs for a profile, newest first.

**Response — 200 OK**

```json
{
  "user_id": "0",
  "reviews": [ { /* clinic row, same shape as POST */ } ]
}
```

---

### POST /resume-clinic/{review_id}/decisions

Record the user's `approve | revise | reject | edit` choice. Uses the same
shared validator as the tailoring decisions; an `edit` requires a non-empty
`edited` payload (the human-authored overhaul). The agent's original
`overhaul_json` is retained for the audit trail.

**Request**

```json
{
  "approval": "approve | revise | reject | edit",
  "edited": { "reorganization": {...}, "rewrites": [...] }
}
```

**Response — 200 OK** — the updated clinic row (same shape as the run response).

**Response — 404** `clinic_review_not_found`.

**Response — 422** Pydantic validation — `approval` must be one of the four
literals; `edited` is required when `approval == "edit"`.

---

### GET /resume-clinic/{review_id}/export

Render the clinic review's final resume in the requested format and stream the
bytes back with a download-friendly `Content-Disposition` header. The renderer
is **deterministic** — no LLM call — and **decision-aware**:

- `decision == "approve"` → apply the agent's `overhaul`.
- `decision == "edit"` → use the human-authored `edited` overhaul.
- `decision == "reject"` → render the original parsed resume unchanged.
- `decision in (null, "revise")` → render with a discreet preview footer noting
  that no decision has been recorded yet.

**Query parameters**

| Name | Type | Notes |
|---|---|---|
| `format` | string | one of `md` (default) / `txt` / `html` / `json` / `docx` / `pdf` |

**Response — 200 OK**

Raw bytes in the requested format. Response headers:

```
Content-Type:        text/markdown; charset=utf-8        (md)
                     text/plain; charset=utf-8           (txt)
                     text/html; charset=utf-8            (html)
                     application/json                    (json)
                     application/vnd.openxmlformats-officedocument.wordprocessingml.document   (docx)
                     application/pdf                     (pdf)
Content-Disposition: attachment; filename="resume_clinic_<8chars>.<ext>"
```

The JSON Resume export follows the [jsonresume.org](https://jsonresume.org)
schema subset: `basics`, `work[]`, `skills[]`, `education[]`, `certificates[]`,
plus a `meta` block carrying the rendered `section_order` and any preview
banner.

**Response — 400** `unsupported_format` — `format` is not in the supported set.

**Response — 404** `clinic_review_not_found` — unknown `review_id`.

**Response — 404** `resume_not_found` — the resume the review points at has
been deleted (the clinic review row is now orphaned; happens if the resume
was removed via `DELETE /users/{user_id}/resume/{resume_id}` but the review
row wasn't cascaded).

---

## Resume management

### DELETE /users/{user_id}/resume/{resume_id}

Hard-delete a resume from a profile and cascade to the resume's clinic
reviews (the past-runs panel would otherwise show broken rows). Job-search
`workflow_runs` and per-call `llm_calls` rows are **preserved** as historical
audit / cost data.

**Response — 200 OK**

```json
{
  "resume_deleted": 1,
  "clinic_reviews_deleted": 2,
  "user_id": 1,
  "resume_id": "..."
}
```

**Response — 404** `unknown_user` — the path `user_id` does not exist.

**Response — 404** `resume_not_found` — the resume id is unknown OR the
resume is owned by a different profile. Same status for both — ADR-062
cooperative scoping, no cross-user enumeration.

**Idempotency** — Re-deleting an already-deleted resume returns 404
`resume_not_found`. The endpoint is not idempotent on success (404 on the
second call); use 404 as the "already gone" signal.

**Related**: to add a fresh resume after a delete, use
`POST /users/{user_id}/resume` (upload). The parser cache is keyed by
`raw_text` SHA-256; a re-upload of the same PDF returns the cached profile,
so a true re-parse under the current parser prompt requires either a
modified source PDF or the resume row having been deleted (this endpoint).

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

### POST /config/ats/verify

Live-check one ATS board token/slug before the Settings UI adds it to a profile's
target-company list (ADR-098 verify-on-add). One bounded GET against the public,
unauthenticated Greenhouse/Lever API; reuses the same check as
`tools/verify_ats_boards.py`. No secrets, no run context.

**Request**

```
POST /config/ats/verify
{"ats": "greenhouse", "slug": "stripe"}
```

`ats` ∈ {`greenhouse`, `lever`} (else `422 unknown_ats`); a blank `slug` is
`422 empty_slug`.

**Response — 200 OK**

```json
{"ats": "greenhouse", "slug": "stripe", "ok": true, "job_count": 42,
 "message": "stripe: 42 open jobs on greenhouse."}
```

A slug that returns 0 jobs / 404 / is unreachable comes back `200` with
`"ok": false, "job_count": 0` and a message — the UI rejects the add without
treating it as a server error.

---

> **Per-profile config (ADR-062).** `GET /config`, `PUT /config`, and
> `GET /config/providers` all resolve the acting profile via `?user_id=` and
> operate on that profile's overrides. `PUT /config` keys each row
> `user_{user_id}__{config_key}` so re-saves upsert in place; protected-key and
> cost-cap rejection are unchanged. A new profile (id ≥ 1) starts with no
> overrides and runs on pure YAML defaults until it sets its own.

---

## Users (ADR-062)

Profile management for multi-user use. No authentication — creating and listing
profiles is open, consistent with the cooperative-isolation model.

### GET /users

List all profiles, default user (`id 0`) first. Backs the sidebar selector.

**Response — 200 OK**

```json
{
  "users": [
    {"id": 0, "name": "Primary", "note": null, "created_at": "2026-05-26T00:00:00Z"},
    {"id": 1, "name": "Alex",    "note": "new-grad SWE", "created_at": "2026-05-26T15:00:00Z"}
  ]
}
```

### POST /users

Create a profile. The id is assigned by the database (auto-increment from 1;
`0` is the reserved pre-existing-data profile).

**Request**

```json
{"name": "Alex", "note": "new-grad SWE, west coast"}
```

`name` is required (1–120 chars, non-blank after trim). `note` is optional
(≤500 chars), human-only metadata the system never acts on.

**Response — 201 Created**

```json
{"user": {"id": 1, "name": "Alex", "note": "new-grad SWE, west coast", "created_at": "..."}}
```

**Errors**: `422 invalid_name` (blank name), `500 persist_failed`.

### PUT /users/{id}

Update a profile's display name / note (the id is never changed). Whitespace-only
note is stored as null.

**Request**

```json
{"name": "Alex", "note": "new-grad SWE, west coast"}
```

**Response — 200 OK**: `{"user": {"id": 1, "name": "Alex", "note": "...", "created_at": "..."}}`

**Errors**: `404 unknown_user`, `422 invalid_name` (blank name), `500 persist_failed`.

### POST /users/{id}/resume

Onboarding step 2: upload a PDF resume for a profile. The file is parsed via the
existing `ResumeParser` path scoped to `{id}`, becoming that profile's active
resume. `multipart/form-data` with a single `file` field.

**Response — 201 Created**

```json
{"resume_id": "…", "file_name": "cv.pdf", "name": "Alex Candidate"}
```

**Errors**: `404 unknown_user` (no such profile), `422 resume_parse_failed`
(parse/extract failure).

**Parsed profile shape (stored in `resumes.parsed_profile_json`)** — the
parser populates a `ResumeProfile` per `app/schemas/resume_profile.py`:

```
name              string?
headline          string?
email             string?
location          string?
summary           string?
experience        [{company, title, start_year, end_year?, description?, technologies[]}]
skills            [string]               # flat list (Scoring Agent + keyword filters read this)
skill_groups      [{category, skills[]}] # ADR-067: categorised view; populated when the source has headings
education         [{institution, degree, year?, gpa?, honors[]}]   # ADR-067: gpa + honors added
certifications    [{name, issuer?, year?}]
raw_text          string                 # full extracted text; Fidelity Reviewer's source of truth
```

`skill_groups` and `EducationEntry.gpa` / `honors` are ADR-067 additions
(2026-05-28). When the source resume has no skill category headings,
`skill_groups` is `[]` and downstream consumers (including the resume
renderer) fall back to the flat `skills` list. The flat list is the union
of all groups' skills when groups are present.

**Cache**: the parser keys its cache on the SHA-256 of `raw_text` scoped to
`user_id`. Re-uploading the same PDF returns the cached profile (no LLM
call). To force a fresh parse under the current parser prompt, either modify
the source PDF or delete the resume row first via
`DELETE /users/{user_id}/resume/{resume_id}`.

---

### DELETE /users/{user_id}/resume/{resume_id}

See [Resume management](#resume-management) above.

---

## Admin (ADR-070 — contract; implementation pending)

### POST /admin/purge

Run the configured data-retention purge (ADR-070, implementing ADR-040). Deletes
rows older than the windows in `config.retention.*` across the PII and
observability tables, cascading a purged `workflow_runs` row to its child rows
(scores, reviews, advice, prep, tailorings, clinic reviews, decisions, events).
The `resumes` row is purged on its own longer window and only when inactive and
not referenced by a non-purged run (see `data_model.md` Section 8A).

**Purge is explicit** — it never runs automatically. This endpoint, the
`tools/purge_data.py` CLI, and a confirm-gated control on the Streamlit Settings
page (which calls this endpoint) are the only triggers; no scheduler exists.

**Request** — no body. Identity via the ADR-062 `?user_id=` seam (acting profile;
purge windows are global, not per-user).

**Response — 200 OK** — the `{table: rows_deleted}` map:

```json
{
  "workflow_runs": 4,
  "job_scores": 31,
  "review_rounds": 12,
  "resume_reviews": 4,
  "career_advice": 9,
  "interview_prep": 3,
  "tailored_resumes": 5,
  "resume_clinic_reviews": 2,
  "human_decisions": 6,
  "step_executions": 40,
  "agent_events": 220,
  "llm_calls": 180,
  "security_events": 0,
  "memory_items": 0,
  "jobs": 18,
  "resumes": 1
}
```

**Idempotency** — Safe to re-run; a second immediate call returns mostly zeros
(the window has not advanced).

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
    "career_track": "all",
    "tracks": ["ic", "architect"]
  }
}
```

Valid `career_track` values: `"ic"` | `"architect"` | `"management"` | `"all"` (default — weights all active tracks equally). `career_track` is the *weighting emphasis*.

`scoring.tracks` (ADR-071) is the per-profile *active-track set* — a subset of `["ic", "architect", "management"]`. Absent/empty/all-invalid = all three (the Primary default). Inactive tracks are not scored (their `*_score` comes back `null`) and do not gate deep review. Distinct from `career_track`; if `career_track` is not in `tracks`, the active set wins.

---

#### TailoringDecisionRequest (`POST /tailorings/{id}/decisions`)

```
approval         "approve" | "revise" | "reject" | "edit"   (required)
edited           object | null   (required only when approval == "edit"; the human-authored draft)
```

| Value | Effect |
|-------|--------|
| `"approve"` | Accept the tailored resume draft as-is; `approved` flips to 1 |
| `"revise"` | Mark the draft as needing changes (re-run tailoring on demand) |
| `"reject"` | Discard the tailored draft |

---

### Response Bodies

#### WorkflowStatusResponse

```
workflow_id       string
status            string            See Status Values
current_step      string | null     Last completed graph node name
run_metrics       object | null     See RunMetrics
errors            array[ErrorEntry] Non-fatal per-job errors
updated_at        string | null     ISO-8601
```

---

#### JobSummaryResponse

```
job_id                    string
title                     string
company                   string
status                    string         Job lifecycle status (e.g. "scored", "shortlisted")
overall_score             int | null     0–100
technical_score           int | null     0–100 (null if track 'ic' inactive, ADR-071)
architecture_score        int | null     0–100 (null if track 'architect' inactive)
leadership_score          int | null     0–100 (null if track 'management' inactive)
domain_score              int | null     0–100
strengths                 array[string]
gaps                      array[string]
recommended_next_action   string | null
```

A track `*_score` is `null` either because the job was not scored at all or because
that track is not active for the profile (ADR-071). The active set is in the run's
`effective_config.scoring.tracks`.

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

The only decision in the system is the out-of-graph tailoring decision,
recorded via `POST /tailorings/{id}/decisions`:

| Field | Value |
|-------|-------|
| `approval` | `approve` \| `revise` \| `reject` \| `edit` |
| `edited` | object (the human-authored draft) — required only when `approval == "edit"` |

The former in-graph decision union (`select_jobs_for_deep_review`,
`approve_tailoring`) was removed in ADR-059.

---

### Status Values

#### Workflow status (`WorkflowStatusResponse.status`)

| Value | Meaning |
|-------|---------|
| `running` | Graph is executing in the background thread pool |
| `completed` | All nodes finished; report is available |
| `failed` | Unrecoverable error; check `errors` array |
| `cancelling` | Cancel requested (ADR-083); run will stop at the next node boundary |
| `cancelled` | Run was cancelled cooperatively; terminal |
| `awaiting_scoring_selection` | Manual-selection run parked after discovery (ADR-060) |

#### Job status (`JobSummaryResponse.status`)

| Value | Meaning |
|-------|---------|
| `discovered` | Job found by discovery service, not yet scored |
| `scored` | Scoring agent has run |
| `shortlisted` | Score ≥ threshold; selected for deep review |
| `reviewed` | Resume Critic + Review Auditor completed |
| `passed` | Excluded from active views via the pipeline filter (ADR-057) |
| `error` | Agent error during processing |
| `skipped` | Skipped due to budget exhaustion |

There are no `applied` / `rejected` / `offer` application-status values — outcome
tracking is out of scope by design (the "No application tracking" rule in `CLAUDE.md`).

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

These limits are enforced by the orchestrator. The scored and discovery caps are
per-run overridable within hard ceilings (ADR-061); the rest are fixed.

| Constant | Value | Enforced where |
|----------|-------|----------------|
| `MAX_JOBS_PER_RUN` | 10 | Default scored cap; per-run override up to `MAX_SCORED_CEILING` = 25 (ADR-061) |
| `MAX_SELECTED_JOBS` | 3 | Auto-selection in `await_job_selection` (qualifying jobs that reach in-graph deep review) |
| `MAX_RESEARCH_STEPS` | 2 | ResearchAgent ReAct loop |
| `MAX_REVIEW_ROUNDS` | 2 | `deep_review` reflection loop |
| `MAX_LLM_CALLS_PER_JOB` | 10 | Per-job budget check in scoring loop |
| `MAX_LLM_CALLS_PER_RUN` | 200 | `check_budget()` called before every agent invocation |

When `MAX_LLM_CALLS_PER_RUN` is hit, remaining unscored jobs are marked `"skipped"`
and the workflow proceeds to `await_job_selection` with whatever jobs are already scored.

---

## HITL Decision Flow

```
POST /workflows
        │
        ▼ (background, no interrupts — ADR-059)
   discover_jobs → load_resume → score_jobs
        │
   await_job_selection      (auto-selects qualifying jobs; no pause)
        │
   deep_review (per selected job) → career_advice
        │
   interview_prep?           (score >= threshold or user requested)
        │
   generate_report → status: completed
        │
   GET /workflows/{id}/report
```

Tailoring is never part of the graph run. It is an out-of-graph operation
(ADR-055) the user invokes on demand after a run, with its own decision:

```
   POST /workflows/{wf}/jobs/{job}/tailorings   (run tailoring + fidelity)
        │
   review draft + fidelity flags
        │
   POST /tailorings/{id}/decisions   { approval: approve|revise|reject|edit }
```
