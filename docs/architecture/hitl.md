# Human-in-the-Loop Model – jobsearchagent-v2

---

## 1. Purpose

This document defines the **Human-in-the-Loop (HITL) model** for `jobsearchagent-v2`.

The system uses agents and workflows to assist with career decisions, but the user remains in control of consequential actions.

The HITL model defines:

* when the workflow pauses
* what decisions the user makes
* how decisions are represented
* how the backend resumes execution
* how decisions are persisted and observed

---

## 2. Core Principle

The system follows this rule:

> Backend owns workflow execution. User owns business decisions. UI collects and submits user decisions.

The user should not decide which internal node runs next.

The user should decide:

* which jobs matter
* whether to continue deeper analysis
* whether to generate interview prep
* whether to accept tailoring suggestions
* whether to save/export results
* whether to update job/application status

The backend converts those user decisions into workflow routing.

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
| mark_job_applied            | User confirms application status       |
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

### 7.7 Application Status Update

If the app tracks job statuses, updates such as “applied” should require explicit confirmation.

The Status Manager is deterministic and non-AI.

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
