# UI Model – jobsearchagent-v2

---

## 1. Purpose

This document defines the user interface model for **jobsearchagent-v2**.

The UI enables the user to:

* discover jobs
* review ranked opportunities
* select jobs for deeper analysis
* understand resume and career gaps
* approve or reject generated outputs
* track workflow progress
* configure preferences
* export reports

The UI must reflect backend workflow state.
It must not orchestrate agents directly.

---

## 2. Core UI Principle

The UI is a **control surface**, not an orchestrator.

The UI can:

* collect inputs
* trigger workflows
* display workflow state
* show results
* collect human decisions
* allow preference configuration

The UI must not:

* decide which agent runs next
* maintain hidden workflow state
* bypass backend validation
* auto-approve outputs
* directly call LLM providers

---

## 3. v1 UI Preservation Strategy

The existing v1 UI already provides:

* job discovery interface
* resume handling
* job listing views
* basic outputs

### Strategy

* **preserve v1 UI structure where possible**
* **refactor backend calls to use v2 workflow services**
* **layer new capabilities on top of existing screens**
* **avoid rewriting UI unless necessary**

### Key Rule

```text
Reuse UI → Replace backend logic → Extend interaction model
```

---

## 4. UI Architecture

```text
Streamlit UI
    ↓
Workflow Service / API Layer
    ↓
Workflow State (SQLite)
    ↓
Agents / Tools / Services
```

The UI:

* reads workflow state
* submits user decisions
* displays results

---

## 5. Primary User Journey

```text
Start
  ↓
Select / upload resume
  ↓
(Optional) Adjust preferences
  ↓
Run job discovery
  ↓
View ranked jobs
  ↓
Select jobs for deep review
  ↓
Review analysis
  ↓
Approve interview prep / tailoring
  ↓
Review final report
  ↓
Export
```

---

## 6. Main Screens

---

## 6.1 Home / Start Screen

### Purpose

Start a workflow.

### UI Elements

* resume selector
* upload resume
* job search criteria
* run button

### Backend Actions

* load profile
* parse resume if needed
* start workflow

---

## 6.2 Settings / Preferences Screen

### Purpose

Allow user to configure behavior without editing `config.yaml`.

---

### Config Model

```text
Effective Config = YAML Defaults + DB Overrides
```

---

### UI Elements

* preferred roles
* preferred locations
* keywords
* excluded keywords
* max jobs to fetch
* max jobs to review
* scoring preference sliders (optional)
* tailoring preference (conservative / moderate)
* save button
* reset to defaults

---

### Backend Sources

* `config.yaml` (defaults)
* `user_config` table (overrides)
* `ConfigService`

---

### Rules

* UI does NOT edit YAML directly
* UI only stores overrides
* backend enforces limits

---

## 6.3 Job Discovery Screen

### Purpose

Show progress of job fetching.

### UI Elements

* progress bar
* job count
* source status
* error messages

---

## 6.4 Job Ranking Screen

### Purpose

Display scored jobs.

### UI Elements

* ranked job list
* score breakdown
* selection controls

### User Actions

* select jobs
* defer jobs
* reject jobs

---

## 6.5 Deep Review Screen

### Purpose

Display analysis for selected job.

### Sections

* job overview
* score breakdown
* strengths
* resume gaps
* career gaps
* research context

---

## 6.6 Reflection Loop View

### Purpose

Show improvement across critique rounds.

### UI Elements

* round number
* audit score
* improvement summary
* stop reason

---

## 6.7 HITL Decision Screen

### Purpose

Collect user decisions.

### UI Elements

* decision message
* options
* relevant context
* action buttons

### Rules

* options come from backend
* decisions are validated

---

## 6.8 Interview Prep Screen

### Purpose

Display interview preparation.

### Sections

* topics
* technical areas
* leadership stories
* weak areas
* prep plan

---

## 6.9 Tailoring Screen

### Purpose

Display resume suggestions.

### Sections

* original text
* suggested text
* evidence
* fidelity risk

### Actions

* approve
* reject
* revise

---

## 6.10 Report Screen

### Purpose

Display final output.

### Sections

* summary
* fit score
* gaps
* advice
* prep
* tailoring
* next steps

### Export

* Markdown
* DOCX
* PDF

---

## 6.11 Run History Screen

### Purpose

View past workflows.

### UI Elements

* workflow list
* job/company
* score
* status
* report link

---

## 6.12 Observability Screen

### Purpose

Show execution details.

### UI Elements

* current step
* agent events
* LLM calls
* tokens/cost
* errors

---

## 7. Navigation Model

```text
1. Start
2. Settings / Preferences
3. Jobs
4. Deep Review
5. Interview Prep
6. Tailoring
7. Reports
8. Run History
9. Observability
```

---

## 8. Workflow State Mapping

| Status           | UI Behavior    |
| ---------------- | -------------- |
| initialized      | show start     |
| running          | show progress  |
| waiting_for_user | show decision  |
| completed        | show report    |
| failed           | show error     |
| cancelled        | show cancelled |

---

## 9. Progress Timeline

Example:

```text
Jobs fetched
Jobs scored
Waiting for selection
Deep review complete
Report ready
```

---

## 10. Error Handling

Display:

* error reason
* recoverability
* next steps

---

## 11. Security & Privacy

Rules:

* no raw resume display unless requested
* no hidden reasoning
* show unsupported claim warnings
* show fidelity risks

---

## 12. UI + HITL

The UI:

* displays decisions
* collects input
* submits structured payloads

The backend:

* validates
* routes workflow
* logs decisions

---

## 13. UI Anti-Patterns

Avoid:

* editing YAML directly
* embedding workflow logic in UI
* auto-approving outputs
* hiding reasoning or scores
* bypassing backend validation

---

## 14. Implementation Structure

```text
app/ui/
  streamlit_app.py
  pages/
    start.py
    settings.py
    jobs.py
    deep_review.py
    interview_prep.py
    tailoring.py
    reports.py
    run_history.py
    observability.py
```

---

## 15. Final Principle

The UI should make the system understandable.

It must clearly show:

```text
what happened
what is happening
what is recommended
what evidence supports it
what decision is required
```

The user should feel informed, not automated over.
