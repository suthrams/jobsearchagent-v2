# Workflow Model – jobsearchagent-v2

---

## 1. Purpose

This document defines all workflows in **jobsearchagent-v2**.

It specifies:

* how data flows through the system
* which agents and services are used
* where decisions are made
* where loops occur
* where human input is required
* when workflows stop

This document is the **execution blueprint** for the system.

---

## 2. Workflow Strategy

The system follows this execution philosophy:

```text
Score many jobs cheaply → Deeply analyze selected jobs
```

All workflows are:

* orchestrator-driven
* state-based
* bounded
* observable
* interruptible via HITL

---

## 3. Primary Execution Flow

```text
User provides search criteria (+ optional resume)
        ↓
Job Discovery Workflow
        ↓
Resume Profile Workflow
        ↓
Scoring Workflow (batch)
        ↓
Shortlist + HITL Selection
        ↓
Deep Review Workflow (per selected job)
        ↓
Optional Interview Prep
        ↓
Optional Tailoring + Fidelity Validation
        ↓
Reporting Workflow
        ↓
Persist + Display Results
```

---

## 4. Job Discovery Workflow

### Purpose

Fetch jobs using automated discovery and normalize them into a common schema.

---

### Inputs

* user search criteria (role, location, keywords)
* job sources (scraper/API config)

---

### Outputs

* normalized job list
* persisted job records

---

### Steps

```text
1. Receive search criteria
2. Call Job Discovery Service
3. Fetch jobs from supported sources
4. Normalize job data into common schema
5. Deduplicate jobs
6. Persist jobs to SQLite
7. Update workflow state with job list
```

---

### Services Used

* job discovery service
* job normalization service

---

### Stop Conditions

* max jobs reached
* no more results from sources

---

## 5. Resume Profile Workflow

### Purpose

Load or create the resume profile used across workflows.

---

### Inputs

* stored resume profile OR
* uploaded resume

---

### Outputs

* structured resume profile
* persisted resume version (if new)

---

### Steps

```text
1. Check if user selected existing profile
2. If new upload:
   a. Parse resume
   b. Extract structured profile
   c. Apply PII minimization
   d. Save new version
3. Load selected profile into workflow state
```

---

### Services Used

* resume parser
* profile extractor

---

### Stop Conditions

* valid profile available

---

## 6. Scoring Workflow

### Purpose

Evaluate multiple jobs against the resume profile.

---

### Inputs

* resume profile
* normalized job list

---

### Outputs

* structured job scores
* ranked job list

---

### Steps

```text
1. For each job:
   a. Call Scoring Agent
   b. Receive structured JobScore
2. Aggregate results
3. Rank jobs by score
4. Persist job scores
5. Update workflow state
```

---

### Agents Used

* Scoring Agent

---

### Constraints

* no ReAct
* no reflection
* batch-friendly
* low-cost operation

---

### Stop Conditions

* all jobs processed
* max jobs reached

---

## 7. Shortlist + HITL Selection

### Purpose

Allow the user to select jobs for deep analysis.

---

### Inputs

* ranked job list

---

### Outputs

* selected job(s)

---

### Steps

```text
1. Present ranked jobs to user
2. Highlight top candidates
3. Request user selection
4. Pause workflow (HITL)
5. Receive user decision
6. Update workflow state
```

---

### HITL Pattern

```text
Pause → Ask User → Resume
```

---

### Stop Conditions

* user selects at least one job
* user cancels workflow

---

## 8. Deep Review Workflow (Core)

### Purpose

Perform high-quality analysis for selected job(s).

---

### Inputs

* selected job
* resume profile
* job score
* workflow state

---

### Outputs

* final review
* career advice
* analysis artifacts

---

### Steps

```text
1. Run Research Agent
2. Run Resume Critic
3. Run Review Auditor
4. Evaluate audit score
5. If needed → repeat Critic + Auditor (reflection loop)
6. Stop when threshold or limits reached
7. Run Career Advisor
8. Persist outputs
```

---

### Agents Used

* Research Agent
* Resume Critic
* Review Auditor
* Career Advisor

---

### Constraints

* bounded ReAct in Research Agent
* bounded reflection loop

---

### Stop Conditions

```text
audit_score ≥ threshold
OR max review rounds reached
OR stagnation detected
```

---

## 9. Reflection Loop (Nested)

### Purpose

Improve critique quality iteratively.

---

### Steps

```text
Resume Critic → Review Auditor → Evaluate → Repeat
```

---

### Inputs

* current review output
* prior feedback

---

### Outputs

* improved review

---

### Limits

```text
MAX_REVIEW_ROUNDS = 3
```

---

### Stop Conditions

* quality threshold reached
* no meaningful improvement
* max rounds reached

---

## 10. Interview Preparation Workflow

### Purpose

Generate targeted interview preparation.

---

### Trigger Conditions

* high match score
* OR user request

---

### Inputs

* job description
* resume profile
* research context
* review outputs

---

### Outputs

* interview preparation plan

---

### Steps

```text
1. Call Interview Coach Agent
2. Generate prep content
3. Persist output
```

---

### Agent Used

* Interview Coach Agent

---

## 11. Tailoring Workflow

### Purpose

Generate improved resume suggestions aligned with a specific job.

---

### Trigger Path

Tailoring runs via a single out-of-graph path (ADR-055; the in-graph path was retired in ADR-059).

| Path | Trigger | When | Approval mechanism |
|------|---------|------|--------------------|
| Out-of-graph router | `POST /workflows/{wf}/jobs/{job}/tailorings` | Post-workflow, per job, on demand | `POST /tailorings/{id}/decisions` writes the `decision` column |

The out-of-graph path is the only path because tailoring intent is per-job, post-hoc, and repeatable — properties that don't fit a single-shot graph lifecycle. There is no in-graph tailoring node and no `interrupt()` in the workflow.

---

### Inputs

* original resume profile
* job description
* `final_resume_review` (from `resume_reviews`, per job)
* `career_advice` (from `career_advice`, per job)

---

### Outputs

* `TailoredResumeDraft` persisted to `tailored_resumes`
* `FidelityReview` persisted alongside the draft in `tailored_resumes.fidelity_review_json`
* User decision persisted as `tailored_resumes.decision` ∈ {approve, revise, reject}

---

### Steps

```text
1. Call Tailoring Agent
2. Generate suggestions (every claim carries supporting_evidence)
3. Call Fidelity Reviewer (always — never skipped)
4. Persist draft + fidelity review to tailored_resumes
5. Surface to user (out-of-graph REST decision; no graph interrupt)
6. Record decision
```

---

### Agents Used

* Tailoring Agent
* Fidelity Reviewer

---

### Constraints

* no fabricated content
* must be evidence-bound
* Fidelity Reviewer must run after every Tailoring Agent call on both paths
* missing experience must be labelled as `claim_type="gap"`, never rewritten as if present

---

## 12. Reporting Workflow

### Purpose

Generate final output for the user.

---

### Inputs

* scoring results
* review outputs
* career advice
* interview prep
* tailored resume

---

### Outputs

* structured report
* downloadable formats

---

### Steps

```text
1. Aggregate all outputs
2. Format report
3. Generate Markdown / DOCX / PDF
4. Persist report
5. Return to UI
```

---

### Services Used

* report generator

---

## 13. Human-in-the-Loop Workflow

### Purpose

Allow user control over decisions.

---

### Pattern

```text
Backend pauses → UI displays → User decides → Backend resumes
```

---

### Decision Points

* job selection
* deep review confirmation (optional)
* tailoring approval
* interview prep trigger
* application status

---

### Requirements

* state must persist pause context
* decisions must be logged

---

## 14. Error Handling Workflow

### Purpose

Handle failures safely.

---

### Types

* LLM failure
* tool failure
* schema validation failure

---

### Strategy

```text
Retry once → Attempt recovery → Fail gracefully
```

---

### Outputs

* error state in workflow
* logged error event

---

## 15. Workflow State Transitions

Each workflow step updates state:

```text
initialized
jobs_fetched
profile_loaded
jobs_scored
awaiting_user_selection
deep_review_in_progress
review_completed
awaiting_tailoring_approval
completed
failed
```

---

## 16. Observability Integration

Each workflow step logs:

```text
workflow_id
step_name
start_time
end_time
status
agent_used
tokens_used
cost
errors
```

---

## 17. Parallelization Strategy (Future-Ready)

Initial execution:

```text
Sequential
```

Future optimization:

```text
Parallel scoring
Parallel research
Parallel deep reviews (multi-job)
```

Design ensures parallelization can be added without changing logic.

---

## 18. Final Workflow Principle

All workflows must follow:

* centralized orchestration
* explicit state transitions
* bounded execution
* structured outputs
* observable execution
* human-controlled decisions

The system must never:

* run uncontrolled loops
* allow agents to coordinate independently
* execute without traceability
* bypass user decisions

---

# End of Document
