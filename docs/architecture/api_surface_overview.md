# API surface — top-level overview

A bird's-eye view of every REST endpoint v2 exposes, grouped by domain.
For the full per-endpoint contract (request/response shapes, error
codes, examples), see [`api_reference.md`](api_reference.md). For the
URL-convention rules (cooperative identity, path-vs-query scoping),
see the **Overview** section there.

## All endpoints at a glance

Eight domains, twenty-nine endpoints. Path parameters are written
without curly braces (`/users/id` rather than `/users/{id}`) so the
diagram renders cleanly; the canonical form with braces lives in the
reference table below.

![API surface — all endpoints grouped by domain](images/api_surface.png)

> The PNG above is rendered deterministically from
> `tools/figure_renderer/specs/api_surface.json` (the same engine as the article
> figures); the JSON spec is the render source of truth and writes straight into
> `docs/architecture/images/` via its `outDir`. Regenerate with
> `python tools/render_figures.py api_surface`. The Mermaid block below is a
> textual mirror kept in sync for inline rendering on GitHub / IDE preview.

```mermaid
flowchart TB
    subgraph Profiles
        p1[GET /users]
        p2[POST /users]
        p3[PUT /users/id]
        p4[POST /users/id/resume]
        p5[DELETE /users/id/resume/rid]
    end

    subgraph Workflows
        w1[POST /workflows]
        w2[GET /workflows/id]
        w3[POST /workflows/id/retry]
        w4[POST /workflows/id/scoring]
        w5[POST /workflows/id/cancel]
    end

    subgraph Workflow_reads
        r1[GET /workflows/id/jobs]
        r2[GET /workflows/id/report]
    end

    subgraph Per_job_on_demand
        d1[POST /workflows/wf/jobs/job/tailorings]
        d2[POST /workflows/wf/jobs/job/deep-review]
        d3[POST /workflows/wf/jobs/job/interview-prep]
        d4[POST /workflows/wf/jobs/job/score]
    end

    subgraph Tailoring_drafts
        t1[GET /workflows/wf/tailorings]
        t2[GET /tailorings/id]
        t3[POST /tailorings/id/decisions]
    end

    subgraph Job_exclusion
        j1[GET /jobs/excluded]
        j2[POST /jobs/id/exclude]
        j3[DELETE /jobs/id/exclude]
    end

    subgraph Resume_Clinic
        c1[POST /users/id/resume-clinic]
        c2[GET /users/id/resume-clinic]
        c3[POST /resume-clinic/id/decisions]
        c4[POST /resume-clinic/id/chat]
        c5[POST /resume-clinic/id/discard-edits]
        c6[GET /resume-clinic/id/export]
    end

    subgraph Config
        f1[GET /config]
        f2[PUT /config]
        f3[GET /config/providers]
        f4[POST /config/reload]
    end

    subgraph Ops_health
        o1[GET /health]
        o2[GET /readyz]
    end
```

## Reference table

Grouped by domain, with the ADR that drives each block and the full path
syntax. **All paths in this table are the canonical form** — with curly
braces.

### Profiles (ADR-062, ADR-067)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/users` | List profiles (default user 0 first). |
| `POST` | `/users` | Create a new profile. Returns the assigned id. |
| `PUT` | `/users/{user_id}` | Update a profile's display name / note. |
| `POST` | `/users/{user_id}/resume` | Upload + parse a PDF resume. Becomes the profile's active resume. |
| `DELETE` | `/users/{user_id}/resume/{resume_id}` | Hard-delete a resume; cascades to its Resume Clinic reviews. Workflow history is preserved. |

### Workflows — job-search runs (ADR-004)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/workflows` | Start a workflow run (202, async). |
| `GET` | `/workflows/{workflow_id}` | Poll workflow status. |
| `POST` | `/workflows/{workflow_id}/retry` | Re-submit a workflow after a server restart (202). |
| `POST` | `/workflows/{workflow_id}/cancel` | Request cooperative cancellation of a running run (202, ADR-083). |
| `POST` | `/workflows/{workflow_id}/scoring` | ADR-060 phase 2: score selected jobs from a manual-selection run (202, async). |

`POST /workflows` also accepts an optional `Idempotency-Key` header (ADR-082): the
same key + body replays the original run instead of starting a second; `retry` and
`scoring` are guarded against concurrent re-submit by an in-flight execution guard.

### Workflow reads — scored jobs + final report

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/workflows/{workflow_id}/jobs` | List scored jobs for a workflow. |
| `GET` | `/workflows/{workflow_id}/report` | Fetch the final report. |

### Per-job on-demand operations (ADR-061)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/workflows/{wf}/jobs/{job}/tailorings` | Create a tailoring draft for any scored job. Runs deep review first if not already present. |
| `POST` | `/workflows/{wf}/jobs/{job}/deep-review` | Run the Resume Critic + Review Auditor reflection loop for one scored job. |
| `POST` | `/workflows/{wf}/jobs/{job}/interview-prep` | Run the Interview Coach for one scored job. |
| `POST` | `/workflows/{wf}/jobs/{job}/score` | ADR-100 Phase 2: research + score one previously-unscored job on demand (e.g. from the Review-later list); it then joins the regular route. Idempotent. |

### Tailoring drafts (ADR-055, ADR-059)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/workflows/{wf}/tailorings` | List tailoring drafts for a workflow. |
| `GET` | `/tailorings/{tailoring_id}` | Fetch one tailoring draft by global id. |
| `POST` | `/tailorings/{tailoring_id}/decisions` | Record `approve` / `revise` / `reject` / `edit`. |

### Job exclusion (ADR-057)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/jobs/excluded` | List jobs explicitly excluded from results. |
| `POST` | `/jobs/{job_id}/exclude` | Exclude a job from scoring / cross-run analytics. |
| `DELETE` | `/jobs/{job_id}/exclude` | Restore a previously-excluded job. |

### Resume Clinic (ADR-066, ADR-068)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/users/{user_id}/resume-clinic` | Run a Resume Clinic review on a resume. |
| `GET` | `/users/{user_id}/resume-clinic` | List past clinic runs for a profile. |
| `POST` | `/resume-clinic/{review_id}/decisions` | Record `approve` / `revise` / `reject` / `edit`. |
| `POST` | `/resume-clinic/{review_id}/chat` | One chat-revise turn (ADR-068). Persists into `edited_json`. |
| `POST` | `/resume-clinic/{review_id}/discard-edits` | Revert chat edits and the decision. |
| `GET` | `/resume-clinic/{review_id}/export` | Render the resume in md / txt / html / json / docx / pdf. |

### Config (ADR-046)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/config` | Effective merged config + protected key list. |
| `PUT` | `/config` | Upsert one user-config override (rejects protected keys). |
| `GET` | `/config/providers` | Available LLM provider catalog. |
| `POST` | `/config/reload` | Rebuild the ModelRegistry after a config write. |

### Ops / health (ADR-084)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness — 200 if the process serves; no dependency I/O. |
| `GET` | `/readyz` | Readiness — probes shared deps; `ready`/`degraded` (200) or `down` (503). |

These two are **unauthenticated** (no `?user_id=`) and **excluded** from `api_requests`
recording. See the [Identity model](#identity-model-adr-062) exemption below.

## Identity model (ADR-062)

Every endpoint resolves the acting profile through **one** dependency,
`get_current_user_id`, which reads an optional `?user_id=` query parameter
(no HTTP headers). Absent → falls back to `"0"`, the pre-existing-data
profile. Reads (history, analytics) and writes (config overrides, run
ownership) are scoped to the resolved id.

The **Profiles** and **Resume Clinic** domains take the acting profile
from the **path** parameter (`/users/{user_id}/...`) instead of the
query-param seam. This is a deliberate exception — FastAPI rejects a
path param named `user_id` co-existing with a `Query`-defaulted
dependency parameter of the same name. The cooperative-scoping rule is
identical; only the syntactic placement differs.

## Two typical user journeys

These chains aren't drawn in the diagram (to keep it clean) but they're
the two end-to-end flows the API was designed around.

### Job-search run, end to end

1. `POST /users` → create a profile (if not 0/Primary).
2. `POST /users/{id}/resume` → upload + parse a resume.
3. `POST /workflows` → start the run (returns workflow_id, 202).
4. Poll `GET /workflows/{id}` until status is `completed`.
5. `GET /workflows/{id}/jobs` → browse scored jobs.
6. `POST /workflows/{wf}/jobs/{job}/tailorings` → draft a tailored
   resume for a specific job (deep review runs first if needed).
7. `POST /tailorings/{id}/decisions` → record approve / revise /
   reject / edit.

### Resume Clinic + chat-edit loop

1. `POST /users/{id}/resume-clinic` → run the clinic on the active resume.
2. Iterate: `POST /resume-clinic/{id}/chat` per turn until happy.
3. Either `POST /resume-clinic/{id}/decisions` with `approval = "edit"`
   to lock the chat state in, or `POST /resume-clinic/{id}/discard-edits`
   to revert.
4. `GET /resume-clinic/{id}/export?format=pdf` (or `docx` / `md` / etc.)
   to download the final resume.

## References

- [`api_reference.md`](api_reference.md) — full per-endpoint contract.
- [ADR-062](adr/ADR-062-multi-user-profiles.md) — the identity seam.
- [ADR-061](adr/ADR-061-configurable-funnel-width.md) — the on-demand
  per-job operations.
- [ADR-066](adr/ADR-066-standalone-resume-clinic.md) and
  [ADR-068](adr/ADR-068-chat-revise-loop-for-the-resume-clinic.md) — the
  Resume Clinic + chat-revise loop.
