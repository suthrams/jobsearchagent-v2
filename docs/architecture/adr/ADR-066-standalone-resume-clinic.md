# ADR-066: Standalone Resume Clinic (Job-Agnostic Review, Advice, and Overhaul)

## Status

Accepted (2026-05-27). Implementation strategy documented below (see
"Implementation Plan"); not yet built. Sequenced as **option (a)** — LLM-only v1
with the `RoleDataProvider` seam stubbed; ESCO/O*NET grounding is a fast-follow.

> **Visual strategy walkthrough:**
> [`docs/architecture/resume_clinic_strategy.md`](../resume_clinic_strategy.md)
> (diagrams: the two surfaces, a clinic run, the data model, the role-data seam,
> and the build sequence). Build is **deferred** while Article 9 is written.

**Decisions locked in design review (2026-05-27):**
- Overhaul output = **structured suggestions** (reorder plan + per-bullet rewrites
  with claim types), reusing the tailoring model/renderer. A **full regenerated
  resume-text document is a nice-to-have / later export**, not v1.
- **One Resume Reviewer agent** produces all analysis + the evidence-bound rewrite
  suggestions; the existing **Fidelity Reviewer** guards the rewrites.
- Quality scorecard is **qualitative + specific findings** (no fake-precise score).
- The **target role/track is pulled from the profile** (not hardcoded). Feedback is
  **seniority-aware via a toggle** (serves Primary too, not just grads).
- Role alignment is **LLM-knowledge based, optionally grounded by a pluggable
  `RoleDataProvider`** (Decision G) so advice isn't limited by stale model
  knowledge.
- The overhaul reuses the **tailoring decision model** (approve / edit / reject;
  human-as-final-author, ADR-059) — leveraging platform we already built.

Builds on ADR-055 (out-of-graph operations), ADR-059 (human-as-final-author; the
Fidelity Reviewer polices the agent, not the human), ADR-062 (per-profile resume),
ADR-015/056 (evidence-bound resume generation), and the ADR-064 context (the funnel
and scoring are senior-tuned) that motivates this.

## Context

The application is a **job-search funnel**: discover -> score -> deep review ->
advice -> tailoring -> interview prep. Every resume-facing agent — Resume Critic,
Review Auditor, Career Advisor, Tailoring Agent — is **job-conditioned**: it runs
inside a per-job flow, assembled around a specific posting and its scores. There is
no path that operates on a resume alone.

For a fresh graduate (and early-career candidates generally) this is the wrong
front door. The scoring rubric is senior-tuned (ADR-064 Decision C), so the funnel
typically returns "nothing qualified," and the outputs that would actually help —
critique, restructuring, positioning advice — are unreachable because they are
gated behind qualifying a job. The value exists in the agents; it is locked behind
discovery and scoring.

This ADR adds a **standalone resume tool** that runs on the resume alone, with no
discovery, no scoring, and no LangGraph funnel. Two clarifying decisions from the
requirements discussion:

1. Include a **reorganize / overhaul** output — a restructured resume draft, not
   just commentary.
2. Give feedback along **two axes**: **resume quality** (structure + impact,
   role-agnostic) and **target-role/track alignment** (how well it positions for a
   chosen role/track).

## Decision

A profile-scoped, **out-of-graph "Resume Clinic"** (same pattern as on-demand
tailoring, ADR-055): resume in -> analysis out, agents run directly, results
persisted; no `interrupt()`, no graph, no discovery/scoring.

### A. Inputs

- The active profile's resume (parsed `ResumeProfile` for the agents; `raw_text`
  for the Fidelity Reviewer's evidence check).
- Optional `target_role` (free text, e.g. "entry-level security analyst") and/or
  `target_track` (`ic` | `architect` | `management`). Absent -> quality-only mode.

### B. Outputs (two axes + an overhaul)

1. **Resume quality review (always):** a scorecard + findings on structure,
   clarity, quantification/impact, ATS-friendliness, consistency, length, and
   early-career framing. Role-agnostic — judged against resume best practice, not a
   posting.
2. **Target-role/track alignment (when a target is given):** how well the resume
   positions for the target, what to emphasize, keyword/skill gaps vs the role
   archetype, and concrete next steps (e.g. certifications, projects). This is
   career positioning, role-targeted but **JD-free**.
3. **Reorganize / overhaul:** a restructured resume draft — section ordering and
   emphasis tuned to the candidate (early-career: projects/education/skills
   forward) plus impact-oriented bullet rewrites. **Evidence-bound**: every
   agent-authored change cites the original resume; missing experience stays
   labeled as a gap, never fabricated.

### C. Agents and the fidelity seam

- A new **Resume Reviewer** agent produces the quality scorecard, the role/track
  alignment, and a **reorganization plan** (structured output). It is job-agnostic
  by construction.
- The **overhaul draft** is evidence-bound generation. The **Fidelity Reviewer**
  stays in the loop and validates the rewritten content against `raw_text` — its
  evidence-binding invariant (ADR-015/056/059) holds with or without a job, so it
  is reused unchanged. A human `edit` remains owner-authored and is not re-reviewed
  (ADR-059).
- Whether the overhaul reuses the Tailoring Agent's evidence-bound generation
  (run job-free) or the new Resume Reviewer emits the draft directly is an
  implementation choice; the ADR fixes the seam: agent-authored rewrites are
  fidelity-checked; the human owns final edits.

### D. Surface

- **Endpoint(s):** an out-of-graph REST operation, e.g.
  `POST /users/{id}/resume-clinic` with an optional `{target_role, target_track}`
  body, returning the review + alignment + overhaul. Mirrors the tailoring router
  shape (read the resume from the repo, run agents directly, persist).
- **UI:** a new **Resume Clinic** sidebar view, scoped to the active profile: pick
  a resume, optionally enter a target role/track, run; render the quality
  scorecard, the role-alignment section, and the reorganized draft (reuse the
  tailoring section/diff renderer). v1 displays + lets the human accept/edit the
  overhaul (human-as-final-author, ADR-059); applying it as a new resume version is
  a later option.

### E. Persistence

A new `resume_clinic_reviews` table keyed by `user_id` + `resume_id` (repeatable;
runs accumulate), storing the review JSON, the alignment JSON, the overhaul draft
JSON, the optional target, and timestamps. Preferred over reusing
`resume_reviews` / `tailored_resumes` with a null `job_id`, which would muddy those
job-keyed tables' semantics.

### G. Role-data enrichment (optional, pluggable) — counters stale LLM knowledge

The role/track alignment axis defaults to the LLM's own knowledge, but model
knowledge of "what an entry-level security analyst needs" can be stale. So role
data is an **optional, pluggable enrichment** behind a `RoleDataProvider` seam
(same spirit as `LLMClient`):

- **Default: none** — LLM-knowledge only (zero new dependencies).
- **`EscoRoleDataProvider`** — the EU ESCO API (free, **no API key**, REST/JSON):
  keyword-search the target role -> occupation URI -> essential/optional skills.
  The low-friction first provider.
- **`OnetRoleDataProvider`** — US DOL CareerOneStop/O*NET API (free, requires a
  registered token + userId): richer per-occupation skills, knowledge, tools &
  technologies, and certifications.

When a provider is configured, the clinic maps the profile's target role to an
occupation, pulls its current required skills/tools/certs, and injects them into
the Resume Reviewer prompt as ground truth for the alignment axis and the
"missing skills/keywords" advice. **Graceful fallback:** if no provider is
configured, the lookup misses, or the call fails/times out, the clinic proceeds on
LLM knowledge alone — never a hard dependency. Division of labor: the API supplies
*what the occupation requires* (current, authoritative); the LLM supplies the
*resume-craft and early-career/seniority positioning* (the taxonomies are
occupation-level, not seniority-level). Whether ESCO ships in v1 or as a
fast-follow is a scope decision (see Consequences).

### F. Non-goals

- Not job-specific tailoring (ADR-055 stays per-posting).
- Not a change to the scoring rubric (ADR-064 Decision C still deferred).
- Not auto-mutating the stored resume — the human owns edits; storing an accepted
  overhaul as a new resume version is a possible follow-up.

## Options considered

- **Standalone out-of-graph clinic (chosen).** Clean second surface; reuses the
  out-of-graph pattern, the per-profile resume, and the fidelity guardrail; leaves
  the funnel untouched.
- **Un-gate the existing critic/advisor to run without a job inside the funnel.**
  Rejected — tangles the job-conditioned flows and their prompts; the clinic is
  clearer as its own operation.
- **Reuse `resume_reviews`/`tailored_resumes` with null `job_id`.** Rejected as the
  store — overloads job-keyed tables; a dedicated table keeps semantics clean.
- **Quality-only (no role axis).** Rejected per the requirement — both axes add
  value; the role axis is simply conditional on a target being supplied.

## Consequences

### Positive

- Directly serves early-career users (and anyone wanting resume help) — the value
  is no longer gated behind a senior-tuned funnel.
- Opens a second product surface (a resume tool) without disturbing the job-search
  funnel; reuses the evidence/fidelity guardrail so the overhaul can't fabricate.

### Tradeoffs

- A real build: a new agent + prompt, an out-of-graph endpoint, a new table, and a
  UI view. A second surface to maintain and document.
- The overhaul carries the same fabrication risk tailoring does — mitigated by
  keeping the Fidelity Reviewer in the loop.

### Neutral

- Docs to add: this ADR + index, `api_reference.md` (the clinic endpoint),
  `data_model.md` (`resume_clinic_reviews`), `agent_model.md` (Resume Reviewer),
  `ui_model.md` (Resume Clinic view), `CLAUDE.md`, `user_guide.md`. Repositions the
  product as job-search **and** resume tooling.

## Implementation Plan

Gated phases, each keeping the suite green. Heavy reuse of platform we already
have (tailoring schema/renderer/decision model, Fidelity Reviewer, ResumeRepository,
ModelRegistry, observability, the out-of-graph runner pattern).

### Phase 1 — Schema + repository

- New table `resume_clinic_reviews`:
  `id` TEXT PK, `user_id` TEXT, `resume_id` TEXT, `target_role` TEXT NULL,
  `target_track` TEXT NULL, `seniority_aware` INTEGER, `review_json` TEXT,
  `alignment_json` TEXT NULL, `overhaul_json` TEXT, `fidelity_review_json` TEXT,
  `decision` TEXT NULL, `edited_json` TEXT NULL, `decided_at` TEXT NULL,
  `created_at` TEXT NOT NULL.
- `ResumeClinicRepository`: `create`, `get_by_id`, `list_by_user`, `record_decision`
  (mirrors `TailoringRepository`).
- `init_db`: add to `_SCHEMA_SQL` + `idx_resume_clinic_user`; add to `reset_db.py`
  `_APP_TABLES`; add a `db_reader` loader for the UI.

### Phase 2 — Agent + schemas

- New `ResumeReviewerAgent` (`AGENT_NAME = "resume_reviewer"`) with prompt
  `app/prompts/agents/resume_reviewer.txt` (always includes shared guardrails).
- Pydantic `ResumeClinicReview` output:
  - `quality`: per-dimension findings (`dimension`, `rating` in
    {strong, adequate, needs_work}, `findings[]`, `fixes[]`) + `overall_summary`.
    Dimensions: structure/ordering, impact/quantification, clarity, ATS/formatting,
    consistency, length-fit, seniority/early-career framing.
  - `alignment` (nullable): `fit_summary`, `missing_skills[]`, `missing_keywords[]`,
    `suggested_certifications[]`, `suggested_projects[]`, `emphasize[]`, `confidence`.
  - `reorganization`: `section_order[]`, `moves[]` (move/cut/promote + rationale).
  - `rewrites`: bullet suggestions in the **same shape tailoring uses**
    (`section_label`, `original_text`, `suggested_text`, `claim_type`,
    `supporting_evidence`, ...) so the Fidelity Reviewer and the existing renderer
    work unchanged.
- Register `resume_reviewer` in `config.yaml` `agents:` + defaults (a Sonnet-class
  model — low-volume, quality-sensitive; not a high-volume cost-capped agent).

### Phase 3 — Out-of-graph runner + RoleDataProvider seam

- `app/services/resume_clinic_runner.py::run_clinic(user_id, resume_id, target_role,
  target_track, seniority_aware, deps)`:
  1. Load resume (parsed profile + `raw_text`) via `ResumeRepository`.
  2. `RoleDataProvider.lookup(role, track)` -> role data or None (default
     `NullRoleDataProvider`; ESCO/O*NET are fast-follow). Inject into the prompt
     when present; otherwise omit the grounding block (graceful fallback).
  3. Run `ResumeReviewerAgent` -> `ResumeClinicReview`.
  4. Run `FidelityReviewer` on `rewrites` against `raw_text` -> fidelity verdict.
  5. Persist via `ResumeClinicRepository`.
- **Observability / cost attribution:** the clinic registers a minimal
  `workflow_runs` row (`workflow_type = "resume_clinic"`, `user_id = profile`) used
  only as the correlation id for `llm_calls`/`agent_events`, so clinic spend is
  attributed to the profile and shows in the per-profile Cost Dashboard (ADR-062).
  It does **not** enter the LangGraph funnel.
- `app/services/role_data/`: abstract `RoleDataProvider.lookup(...) -> RoleData | None`
  + `NullRoleDataProvider` (v1 default). `Esco`/`Onet` providers land in the
  fast-follow.

### Phase 4 — API

- New `app/api/routers/resume_clinic.py`:
  - `POST /users/{id}/resume-clinic` `{resume_id?, target_role?, target_track?,
    seniority_aware?}` (resume_id defaults to the profile's active resume) -> runs
    the clinic, returns the review.
  - `GET /users/{id}/resume-clinic` -> past runs.
  - `POST /resume-clinic/{review_id}/decisions` -> approve/revise/reject/edit,
    reusing the tailoring decision validation (`edit` carries the human draft, not
    re-reviewed; ADR-059).
- Register the router in `main.py`; resolve agents/repos via `get_deps`.

### Phase 5 — UI

- New **Resume Clinic** sidebar view (active profile): resume picker, target
  role/track inputs (pre-filled from the profile), **seniority-aware toggle**,
  "Run clinic" button. Render the quality scorecard + alignment + reorganization
  plan, and the `rewrites` via the **existing** `_render_tailored_sections` /
  `_render_one_bullet` renderer with the approve/edit/reject decision flow.

### Phase 6 — Tests + docs

- Tests: repository CRUD; `run_clinic` with mocked reviewer + fidelity (asserts
  Fidelity always runs on rewrites); endpoint success / decision / unknown
  user-or-resume; schema validation; the clinic `workflow_runs` row is written with
  the right `user_id`.
- Docs: `data_model.md` (new table -> 20 tables), `api_reference.md` (endpoints),
  `agent_model.md` (Resume Reviewer), `ui_model.md` (Resume Clinic view),
  `CLAUDE.md` (agents table + invariants: clinic is out-of-graph, rewrites are
  fidelity-checked), `user_guide.md`, `CHANGELOG.md`.

### Fast-follow (separate ADR-light change)

`EscoRoleDataProvider` (no key) then `OnetRoleDataProvider` (CareerOneStop token):
map the target role -> occupation -> required skills/tools/certs, injected as
ground truth for the alignment axis. Config-gated; graceful fallback to LLM-only.

## References

- ADR-055 — On-demand tailoring as an out-of-graph operation (the pattern this
  follows).
- ADR-059 — Retire in-graph HITL; human-as-final-author; the Fidelity Reviewer
  polices the agent.
- ADR-015 / ADR-056 — Evidence-bound resume generation (the invariant the overhaul
  inherits).
- ADR-062 — Per-profile resume (the clinic's input).
- ADR-064 — Per-profile discovery + the senior-tuned-scoring context that motivates
  a job-agnostic resume path.

### External role-data sources (Decision G)

- ESCO API (EU; free, no key): https://esco.ec.europa.eu/en/use-esco/use-esco-services-api
- CareerOneStop Web API (US DOL; free, registration) wrapping O*NET:
  https://www.careeronestop.org/Developers/WebAPI/web-api.aspx
  (Get Occupation Details returns skills/knowledge/abilities/tools/certifications.)
