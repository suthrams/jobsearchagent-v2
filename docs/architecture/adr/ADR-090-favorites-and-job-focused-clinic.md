# ADR-090: My Favorite Jobs + Job-Focused Resume Clinic

## Status

Accepted (2026-06-07). Companion spec:
[`favorites_job_focused_clinic_spec.md`](../favorites_job_focused_clinic_spec.md)
(the reviewable design: data model, API, UI, test plan). Signed off; implementing.

Builds on ADR-066 (Resume Clinic), ADR-059 (evidence-bound tailoring), ADR-072
(tailoring live chat + export), ADR-062 (per-profile identity), ADR-075 (UI reads via
the API), ADR-070 (retention). Threads the CLAUDE.md "No application tracking" rule
and the ADR-088 §E guardrail.

## Context

The Resume Clinic (ADR-066) improves a resume **job-agnostically** — it takes an
optional `target_role`/`target_track`, never a specific job. Job-*specific* tailoring
already exists, but only on the **Opportunity** page (ADR-059 Tailoring Agent +
Fidelity Reviewer, ADR-072 live chat + export), reached job-first.

Job seekers want the inverse, resume-first, workflow: *flag a few interesting roles,
then sit in the resume workspace and tailor toward one of them.* Two gaps block it:

1. There is **no way to mark a job** to come back to it.
2. The **Resume Clinic has no job focus** — so a clinic session can't produce a
   resume tailored to a specific role.

The first gap is where the design must be careful. A "saved / favorited jobs" set is
exactly the shape the project deliberately avoided: **no application tracking**, so
the human stays the career decision-maker (CLAUDE.md; ADR-088 §E forbids a
`pursuing/shortlist/saved` set; a guardrail test enforces it). The owner has decided
to add a bounded **"My favorite jobs"** set, and this ADR records *how* it stays a
filter-input and not a tracker.

## Decision

### A. "My favorite jobs" — a bounded, status-free filter-input

Add a per-profile set of favorited jobs, capped at **25**. A favorite stores **only**
`{user_id, workflow_id, job_id, title, company, created_at}` — a job reference plus a
display snapshot plus a timestamp. It carries **no** `apply/applied/status/
pursuing/stage/outcome` field, now or ever. It is a *signal the user gives the system*
("tailor toward these"), in the same family as the ADR-057 **exclude** filter — the
positive counterpart. New table `favorite_jobs`, `FavoriteRepository`, and three
path-scoped endpoints under `/users/{id}/favorites` (the ADR-066 per-user family).
Favorite/un-favorite from **Matches** (the selected-row action cluster) and the
**Opportunity** header.

The boundary is enforced two ways: a **schema forcing-function test** (the table's
columns must be exactly the spec'd set — adding a status column fails the build) and
the **extended no-tracking UI scan** (favorites/clinic surfaces must not contain
`apply/applied/status/pursuing/stage`; "favorite" is the one sanctioned positive
word).

### B. Job-focused Resume Clinic — reuse the tailoring engine

The Resume Clinic gains an **optional** "Focus a job (from My favorite jobs)"
selector:

- **No focus** -> unchanged: today's job-agnostic `ResumeReviewerAgent` flow.
- **Focus** -> the clinic renders the **existing** job-specific tailoring flow for the
  favorited job's `(workflow_id, job_id)`: the Tailoring Agent + Fidelity Reviewer
  (ADR-059), its drafts + approve/revise/reject/edit decisions, the ADR-072 live
  chat, and multi-format export. Output = a resume tailored to that role.

This **reuses** the proven tailoring path rather than building a second one; the
clinic becomes a resume-first entry point into it. Net new backend = the favorites
CRUD only. The JD comes from the job's stored `job_description`; a purged run degrades
the focus gracefully (re-pick / job-agnostic fallback).

### C. Retention

Favorites are **user-owned working data**: a run purge (ADR-070) does **not** cascade
to favorites (the snapshot persists so the dropdown still shows the role); deleting a
**profile** removes its favorites. A documented exception to the run-purge cascade.

### D. Non-goals

- No application status, stage, or outcome tracking (the boundary).
- No new agent, workflow node, or in-graph change.
- No dedicated Favorites nav screen in v1 (toggle + clinic dropdown only).
- The clinic focus produces a tailored resume; it does not also re-point the
  job-agnostic scorecard at the job (possible future enhancement).

## Options considered

- **Don't build it / keep tailoring only on Opportunity.** Rejected: the resume-first
  loop (flag roles, then tailor toward one) is a real job-seeker workflow the product
  lacks.
- **A general "Saved jobs" board with status.** Rejected: that is the application
  tracker the product deliberately omits (CLAUDE.md), and it puts the career decision
  in the tool. The bounded, status-free favorites set keeps the human as
  decision-maker.
- **A new "clinic tailoring" agent / endpoint.** Rejected: the Tailoring Agent +
  Fidelity + chat + export already produce exactly the desired output; a second path
  would split the tailoring flow and double the test surface. Reuse instead.
- **Bounded favorites + reuse tailoring (chosen).** Maximizes value for minimal
  net-new surface (favorites CRUD + UI composition), and keeps the no-tracking
  boundary explicit and enforced.

## Consequences

### Positive

- The resume-first loop works: favorite -> focus the clinic -> tailored resume ->
  export, per profile.
- Almost no new backend (favorites CRUD); the tailoring engine is reused, so behavior
  and tests stay shared with the Opportunity page.
- The no-tracking rule is upheld *and* made explicit, with two enforcing tests.

### Tradeoffs

- A new wired table + endpoints to maintain and purge-scope.
- Favorites deliberately survive run purge, so a favorite can outlive its JD; the UI
  must handle the unavailable-JD case.
- The "favorite" affordance is one click from drifting toward status tracking in
  future work; the guardrail tests are the standing defense.

### Neutral

- Docs: this ADR + index; the companion spec; `data_model.md` (the table + retention
  exception); `api_reference.md` (the 3 endpoints); `ui_architecture.md` (the toggle +
  clinic focus); `user_guide.md` (the flow); `wiki.md` reachability.
- Tests: favorites repo/API unit tests; the schema forcing function; the extended
  no-tracking scan; UI smoke for the new controls.

## References

- ADR-066 - Resume Clinic (the job-agnostic flow the focus extends).
- ADR-059 / ADR-072 - evidence-bound tailoring + live chat + export (reused).
- ADR-057 - per-job exclude (the filter-input precedent; favorites is its positive).
- ADR-062 / ADR-075 / ADR-070 - identity / API-only reads / retention.
- CLAUDE.md "No application tracking" + [[feedback_filter_vs_tracker_distinction]] -
  the boundary this ADR threads.
