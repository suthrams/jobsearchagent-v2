# Human-in-the-Loop Model – jobsearchagent-v2

---

> **Status note (ADR-059).** The in-graph interrupt-resume HITL model was **retired**:
> the workflow no longer calls `interrupt()` and there is no `waiting_for_user` pause.
> **The sections immediately below — Purpose, Core Principle, and the current HITL
> surface — are the authoritative model** (out-of-graph decisions plus one
> between-phase scoring triage). Everything under "Historical design" is the
> retired design, kept for context only. See also `CLAUDE.md` (HITL rules),
> `api_reference.md`, and ADR-055 / 059 / 060 / 061 / 066 / 083.

---

## 1. Purpose

This document defines the **Human-in-the-Loop (HITL) model** for `jobsearchagent-v2`.

The system uses agents and workflows to assist with career decisions, but the user
remains in control of consequential decisions. As of ADR-059 the model is
**out-of-graph**: the workflow runs end to end with no `interrupt()` and no
`waiting_for_user` pause.

---

## 2. Core Principle

> Backend owns workflow execution. User owns business decisions. UI collects and submits user decisions.

The user does not decide which internal node runs next. The user decides:

* which jobs to score, when manual scoring triage is on (ADR-060)
* whether to run deep review, interview prep, or tailoring on a scored job (ADR-061)
* whether to accept a tailored draft: `approve / revise / reject / edit` (ADR-055/059)
* whether to accept Resume Clinic rewrites (ADR-066)
* whether to cancel a running workflow (ADR-083)

There is no Apply / Save / application-status decision — that is out of scope by
design (the "No application tracking" rule in `CLAUDE.md`). The backend converts each
user decision into persistence and, where relevant, the next on-demand operation; it
never resumes a paused graph, because the graph never pauses.

---

## The current HITL surface (authoritative)

| Form | Trigger | Decision |
|------|---------|----------|
| Auto-selection | in-graph, automatic | none — top-N qualifying jobs picked for deep review, no pause (ADR-054/059) |
| Manual scoring triage | `scoring.manual_selection` on; run parks at `awaiting_scoring_selection` | `POST /workflows/{id}/scoring` with the chosen job ids (ADR-060) |
| On-demand tailoring | `POST /workflows/{wf}/jobs/{job}/tailorings` | `POST /tailorings/{id}/decisions` -> approve / revise / reject / edit (ADR-055) |
| On-demand deep review / interview prep | `POST .../deep-review`, `POST .../interview-prep` | none beyond triggering (ADR-061) |
| Resume Clinic | `POST /users/{id}/resume-clinic` | `POST /resume-clinic/{id}/decisions` (ADR-066) |
| Run cancellation | `POST /workflows/{id}/cancel` | cooperative; stops at the next node boundary (ADR-083) |

Decisions are validated by the backend before persisting, recorded on the domain table
AND the cross-cutting `human_decisions` audit table (ADR-074, now wired), and surfaced
in the System Dashboard. The UI never auto-approves agent output. A human `edit`
decision makes the user the accountable author and is not re-reviewed by the Fidelity
Reviewer (ADR-059).

---

# Historical design (retired in ADR-059 — kept for context only)

> Everything below describes the original interrupt-resume design: in-graph
> `interrupt()`, `WorkflowState.status = waiting_for_user`, `pending_decision`, and
> `POST /workflows/{id}/decisions`. **None of it is wired today.** It is preserved to
> explain how the model evolved; for current behaviour see the sections above.

---

## 3. HITL Execution Pattern

```text
Backend workflow runs
        ↓
Backend reaches decision point
        ↓
Backend sets status = waiting_for_user
        ↓
Backend stores pending_decision
        ↓
UI displays decision request
        ↓
User selects option
        ↓
UI submits decision
        ↓
Backend validates decision
        ↓
Backend persists human_decision
        ↓
Backend clears pending_decision
        ↓
Backend resumes workflow
```

---

## 4. HITL State Model

When user input is required, the workflow state should include:

```json
{
  "status": "waiting_for_user",
  "current_step": "awaiting_job_selection",
  "pending_decision": {
    "decision_type": "select_jobs_for_deep_review",
    "message": "Select jobs to move into deep review.",
    "options": ["approve", "reject", "defer"],
    "payload": {
      "candidate_jobs": []
    }
  }
}
```

---

## 5. Decision Object

A submitted decision should use a structured format.

```json
{
  "workflow_id": "wf_123",
  "decision_type": "select_jobs_for_deep_review",
  "decision_value": "approve",
  "payload": {
    "selected_job_ids": ["job_001", "job_002"]
  },
  "decided_at": "2026-01-01T10:00:00Z"
}
```

---

## 6. Decision Types

| Decision Type               | Purpose                                |
| --------------------------- | -------------------------------------- |
| select_jobs_for_deep_review | User chooses jobs for deeper review    |
| approve_deep_review         | User confirms deeper analysis          |
| request_interview_prep      | User requests interview prep           |
| approve_tailoring           | User approves tailored resume draft    |
| reject_tailoring            | User rejects tailored resume draft     |
| request_tailoring_revision  | User asks for revised tailoring        |
| approve_report_export       | User approves report generation/export |
| cancel_workflow             | User cancels workflow                  |
| defer_job                   | User defers a job for later            |

---

## 7. HITL Checkpoints

### 7.1 Job Selection

After scoring, the workflow should pause and ask the user which jobs should move forward.

```text
Ranked jobs → user selects jobs → backend resumes deep review
```

Why:

* avoids deep review on irrelevant jobs
* controls cost
* keeps the user focused on roles they care about

---

### 7.2 Deep Review Approval

The system may ask for explicit approval before running expensive deep analysis.

Useful when:

* many jobs are selected
* estimated cost is high
* match score is borderline
* user wants more control

---

### 7.3 Interview Prep Decision

Interview prep should run when:

* match score is high
* user explicitly requests it
* deep review suggests interview prep would be valuable

The user should be able to request or skip it.

---

### 7.4 Tailoring Approval

Resume tailoring should always involve user approval.

The system may suggest:

* summary changes
* bullet rewrites
* skill section adjustments
* positioning improvements

But the user must approve before output is treated as final.

---

### 7.5 Fidelity Review Resolution

If the Fidelity Reviewer flags unsupported claims, the workflow should pause.

User options:

```text
remove unsupported claims
revise suggested text
reject tailoring draft
accept only safe suggestions
```

The backend should not silently approve unsafe tailoring.

---

### 7.6 Report Export Approval

Before exporting a final resume or report, the user may approve:

* content
* format
* included sections

---

### 7.7 Application Status Update — never in scope

This was sketched in the original design but **never built and never planned**:
application/status tracking (apply date, "marked applied") is out of scope by design.
See the "No application tracking" rule in `CLAUDE.md`.

---

## 8. Backend Responsibilities

The backend is responsible for:

* detecting decision points
* creating pending decision requests
* pausing workflows
* validating submitted decisions
* persisting decisions
* resuming workflows
* logging observability events
* enforcing safety rules

The backend must not assume user approval.

---

## 9. Frontend Responsibilities

The frontend is responsible for:

* displaying decision requests
* showing relevant context
* collecting user choice
* submitting decisions to backend
* showing updated workflow status

The frontend must not decide workflow routing.

---

## 10. Decision Validation

All submitted decisions must be validated.

Validation rules:

* decision type must match pending decision
* decision value must be one of allowed options
* referenced job IDs must exist
* workflow must be in waiting_for_user state
* decision payload must match schema

Invalid decisions should be rejected.

---

## 11. Persistence

Human decisions should be stored in:

```text
human_decisions
```

Recommended schema:

```sql
CREATE TABLE human_decisions (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    decision_value TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
```

---

## 12. Observability

Every HITL event should be logged.

Events:

```text
hitl.pause_created
hitl.decision_displayed
hitl.decision_submitted
hitl.decision_validated
hitl.workflow_resumed
hitl.decision_rejected
```

Captured fields:

```text
workflow_id
decision_type
decision_value
current_step
timestamp
```

---

## 13. HITL and Security

HITL is also a safety control.

It prevents:

* automatic use of hallucinated content
* auto-application to jobs
* unapproved resume changes
* accidental status updates
* costly workflows running without user awareness

---

## 14. HITL and Ethics

The system should not present its outputs as commands.

Use language like:

```text
Suggested next step
Recommended option
Approve before applying
Review before export
```

Avoid:

```text
You must do this
You are not qualified
This is the correct answer
```

---

## 15. HITL and Cost Control

The backend may pause before expensive workflows.

Example:

```json
{
  "decision_type": "approve_deep_review",
  "message": "This action may run multiple LLM calls. Continue?",
  "options": ["approve", "reject", "defer"]
}
```

This gives the user cost awareness.

---

## 16. HITL and Workflow Resume

After a valid decision:

1. persist human decision
2. clear `pending_decision`
3. update workflow status from `waiting_for_user` to `running`
4. determine next step
5. resume workflow execution

---

## 17. Anti-Patterns to Avoid

Avoid:

* UI orchestrating agents directly
* backend assuming approval
* hidden approvals
* unvalidated decisions
* auto-tailoring without review
* auto-applying to jobs
* treating user decision as LLM memory without consent
* storing sensitive decision context unnecessarily

---

## 18. Example End-to-End HITL Flow

```text
Scoring workflow completes
        ↓
Backend ranks jobs
        ↓
Backend sets pending decision:
"Select jobs for deep review"
        ↓
UI displays ranked jobs
        ↓
User selects two jobs
        ↓
UI submits decision
        ↓
Backend validates selected job IDs
        ↓
Backend persists human_decision
        ↓
Backend resumes deep review workflow
```

---

## 19. Final Principle

HITL is not a UI feature.

It is a workflow control mechanism.

The user provides judgment.

The backend controls execution.

The system remains useful, safe, and accountable because consequential decisions are explicit.
