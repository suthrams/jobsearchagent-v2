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

## 6.0 Profile selector + onboarding (ADR-062)

### Purpose

Pick whose search this is, and add new profiles. The app serves multiple
job-seekers from one install under sequential use.

### UI Elements

* a sidebar **Profile** dropdown (over `GET /users`) that sets
  `st.session_state["current_user_id"]` and is mirrored onto the API client and
  the `db_reader` read path
* an **Add profile** button opening a 3-step onboarding wizard: identity
  (name + optional note -> `POST /users`) -> resume upload (scoped to the new
  profile) -> default roles/locations (saved as that profile's `user_config`)

### Behavior

* Switching profiles re-scopes every read view (history, analytics, cost) and the
  Start New Run resume picker, and tags new runs with the selected owner.
* The default profile (id 0) owns all pre-existing data.

### Rule

* Identity flows through ONE client seam (`api_client.set_user_id`) mirroring the
  backend `get_current_user_id` dependency — views never attach `user_id`
  themselves. This is the cooperative-isolation model (see §11).

### "Manage an existing profile" sub-panel

A second area within the Profiles view, with three expanders:

* **Edit a profile (name / note)** — `PUT /users/{id}` (display name + note).
* **Add a resume to a profile** — `POST /users/{id}/resume`. Parses the PDF;
  the new resume becomes the profile's active resume.
* **Delete a resume from a profile** — `DELETE /users/{id}/resume/{resume_id}`.
  Profile selector -> resume picker (`load_user_resumes`) -> a count of clinic
  reviews that would cascade (via `api.list_resume_clinic_runs`) -> a
  confirm checkbox that gates the **Delete resume** button. The cascade
  also removes the resume's `resume_clinic_reviews` rows; job-search
  `workflow_runs` and per-call `llm_calls` rows are preserved.

  **Why the delete-then-reupload flow exists:** `ResumeParser` caches the
  parsed profile keyed by the PDF text hash, so re-uploading the same PDF
  returns the cached profile (no LLM call). Deleting the resume row clears
  the cache for that profile and forces a fresh parse — useful after a
  parser-prompt upgrade (e.g. ADR-067 added GPA / honors / skill groups
  the previous parser had no slot for).

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

## 6.13 Resume Clinic (ADR-066)

### Purpose

A standalone, job-agnostic resume review for the active profile. Runs the
clinic on the resume alone (no discovery, no scoring, no JD) and renders
the quality scorecard, optional role/track alignment, reorganization plan,
and evidence-bound rewrites. Out-of-graph; serves early-career candidates
that the senior-tuned funnel underserves.

### UI Elements

* Active profile (read-only header)
* Resume picker (active resume preselected)
* Target role free-text input (pre-filled from `profile.search.titles[0]`)
* Target track selector (`-` / `IC` / `Architect` / `Management`)
* Seniority-aware toggle (calibrates feedback to the candidate's stage)
* "Run clinic" primary button
* Results pane (after run):
  * Quality scorecard — one expander per dimension with a rating chip,
    findings, and fixes
  * Role/track alignment — fit summary, missing skills/keywords/certs,
    suggested projects, items to emphasize, confidence chip
  * Reorganization plan — proposed section order + per-move
    `move / cut / promote` items with rationale
  * Rewrites — side-by-side `original` vs `suggested` with claim-type chip
    (`restate | reorder | quantify | reframe`) and supporting-evidence
    caption
  * Fidelity verdict — `approve | revise | reject` chip + confidence, plus
    the caveat that the reviewer is tailoring-tuned (clinic-mode follow-up
    documented in ADR-066)
* Decision controls (Approve / Revise / Reject) — POSTs to
  `/resume-clinic/{id}/decisions`. Inline `edit` editor is a follow-up.
* **Export the final resume** panel — format selectbox over
  `md / txt / html / json / docx / pdf`, an inline preview expander for
  text-y formats, and a download button. Hits
  `GET /resume-clinic/{id}/export?format=...` per format change. The
  renderer is **deterministic** and **decision-aware**: `approve` applies
  the agent's overhaul, `edit` uses the human draft, `reject` falls back
  to the original parsed resume, undecided / `revise` shows a preview
  banner. See `app/services/resume_text_renderer.py`.
* Past clinic runs — expander per row, button to reload into the results pane.

### Backend Actions

* `api_client.run_resume_clinic` -> `POST /users/{user_id}/resume-clinic`
* `api_client.list_resume_clinic_runs` -> `GET /users/{user_id}/resume-clinic`
* `api_client.submit_resume_clinic_decision` -> `POST /resume-clinic/{id}/decisions`
* `api_client.export_resume_clinic` -> `GET /resume-clinic/{id}/export?format=...`
* Read path: `db_reader.load_user_clinic_reviews(user_id)` for the
  past-runs panel.

### Rules

* The path's `{user_id}` is the active profile (cooperative scoping per
  ADR-062). The endpoint does NOT consult the query-param identity seam
  because of a FastAPI path-vs-query name collision; this is the only
  route family in v2 that takes the active id from the path.
* No raw resume text ever leaves this view to the agent — the API resolves
  the resume on the backend and the reviewer receives only the parsed
  profile (raw_text goes to fidelity only).
* The reviewer prompt's quality dimensions and claim-type values are
  Literal-enforced; a schema-validation failure on a clinic run is shown
  as a backend 502.

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

**Multi-user isolation is cooperative, not enforced (ADR-062).** The profile
selector decides *which* profile's data a view reads and writes; with no
authentication it is not an access-control boundary. The UI must not present it
as one. The single identity seam (`api_client.set_user_id` /
`get_current_user_id`) is where real enforcement attaches if auth is added later.
See `security.model.md` §4.1.

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
