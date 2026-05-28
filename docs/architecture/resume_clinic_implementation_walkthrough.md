# Resume Clinic — Implementation Walkthrough

Companion to [ADR-066](adr/ADR-066-standalone-resume-clinic.md) and
[resume_clinic_strategy.md](resume_clinic_strategy.md). Where the ADR locks the
**decision** and the strategy doc shows the **diagrams**, this doc names the
**files, signatures, and tests** for each of the six phases, plus deviations
from the ADR I caught during planning. Read this before approving Phase 1.

## Build conventions (apply to every phase)

- **Pacing:** phase-by-phase. Each phase ends with the suite green, one commit,
  one push, and a check-in before the next phase starts. (User decision,
  2026-05-28.)
- **Tests in CI = unit only.** Live API calls happen only in the Phase-6
  notebook. Matches the rest of v2.
- **Model pin discipline.** Any new agent's `(provider, model)` is added to
  `tests/model_pins.json` so the build-time invariant from commit `e31cee1`
  covers it. The pin discipline is the build-time gate; the notebook is the
  audit/inspection surface — same audit-vs-gate split as ADR-058.
- **Prompt rule.** Every new prompt is loaded through `PromptLoader`, which
  auto-injects `prompts/shared/guardrails.txt`. The agent receives parsed
  resume profile, never raw resume text.
- **Decision model.** The clinic reuses the tailoring decision model
  (`approve | revise | reject | edit`); `edit` is human-authored and is not
  re-reviewed by Fidelity (ADR-059).

---

## Phase 1 — Schema + repository

**Goal:** add the `resume_clinic_reviews` table and `ResumeClinicRepository` so
later phases have a place to write to.

### Files

**New**
- `app/repositories/resume_clinic_repository.py` — repository class.
- `tests/v2/test_resume_clinic_repository.py` — repo CRUD tests.

**Modified**
- `app/repositories/database.py` — add table to `_SCHEMA_SQL`,
  `idx_resume_clinic_user` index.
- `reset_db.py` — add `resume_clinic_reviews` to `_APP_TABLES` so the reset
  command wipes it like every other app table.
- `tests/v2/test_repositories.py` — add the new table to `_EXPECTED_TABLES`.
- `app/ui/db_reader.py` — add `load_user_clinic_reviews(user_id)` loader so
  Phase 5 can read past runs directly (mirrors the existing read-path pattern).

### Schema

```sql
CREATE TABLE IF NOT EXISTS resume_clinic_reviews (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,                 -- owning profile (decimal-string)
    resume_id TEXT NOT NULL,
    workflow_run_id TEXT,                  -- for cost attribution (see deviation)
    target_role TEXT,                      -- absent -> quality-only mode
    target_track TEXT,                     -- ic | architect | management
    seniority_aware INTEGER NOT NULL DEFAULT 0,
    review_json TEXT NOT NULL,             -- quality scorecard (always)
    alignment_json TEXT,                   -- null when no target given
    overhaul_json TEXT NOT NULL,           -- reorganization + rewrites
    fidelity_review_json TEXT,
    decision TEXT,                         -- approve | revise | reject | edit
    edited_json TEXT,                      -- only when decision = edit
    decided_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resume_clinic_user
    ON resume_clinic_reviews(user_id);
```

### Repository signatures

```python
class ResumeClinicRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH): ...

    def create(self, clinic_id: str, user_id: str, resume_id: str, *,
               workflow_run_id: str | None,
               target_role: str | None, target_track: str | None,
               seniority_aware: bool,
               review: dict,
               alignment: dict | None,
               overhaul: dict,
               fidelity_review: dict | None) -> None: ...

    def get_by_id(self, clinic_id: str) -> dict | None: ...

    def list_by_user(self, user_id: str) -> list[dict]: ...

    def set_decision(self, clinic_id: str, decision: str,
                     edited: dict | None = None) -> None: ...
```

### Tests

- `test_resume_clinic_reviews_in_expected_tables`
- `test_create_and_get_by_id`
- `test_get_by_id_returns_none_for_unknown_id`
- `test_list_by_user_returns_newest_first`
- `test_list_by_user_returns_empty_for_unknown_user`
- `test_create_with_no_target_persists_null_alignment`
- `test_set_decision_approve_records_decision_and_timestamp`
- `test_set_decision_edit_persists_edited_json`
- `test_set_decision_reject_does_not_persist_edited_json`

### Deviations from ADR-066

- **Added `workflow_run_id` column** (nullable). The ADR's column list omitted
  it, but the Phase-3 runner writes a lightweight `workflow_runs` row for cost
  attribution; storing the FK here makes the audit join trivial.

### Commit

`feat(resume-clinic): Phase 1 — schema and repository for resume_clinic_reviews`

---

## Phase 2 — ResumeReviewerAgent + schemas + prompt

**Goal:** build the Resume Reviewer agent with its Pydantic output schema and
prompt; register it in config.yaml; pin it.

### Files

**New**
- `app/schemas/resume_clinic.py` — `ResumeClinicReview` + nested models.
- `app/agents/resume_reviewer.py` — `ResumeReviewerAgent(BaseAgent)`.
- `app/prompts/agents/resume_reviewer.txt` — versioned prompt body.
- `tests/v2/test_resume_reviewer_agent.py` — agent contract tests.

**Modified**
- `config/config.yaml` — register `resume_reviewer` in `agents:` block with a
  Sonnet-class default (`claude-sonnet-4-6`). Not in `HIGH_VOLUME_AGENTS`.
- `config/config.example.yaml` — same entry mirrored.
- `tests/model_pins.json` — add a pin row for `resume_reviewer` (today's date).

### Agent

```python
class ResumeReviewerAgent(BaseAgent):
    AGENT_NAME = "resume_reviewer"

    def run(self, workflow_id: str, context: dict) -> ResumeClinicReview:
        result = self._run(workflow_id, context, ResumeClinicReview)
        return ResumeClinicReview(**result)
```

### Schemas

```python
class QualityDimension(BaseModel):
    dimension: Literal[
        "structure_ordering", "impact_quantification", "clarity",
        "ats_formatting", "consistency", "length_fit",
        "seniority_framing",
    ]
    rating: Literal["strong", "adequate", "needs_work"]
    findings: list[str]
    fixes: list[str]

class ResumeQuality(BaseModel):
    dimensions: list[QualityDimension]
    overall_summary: str

class Alignment(BaseModel):
    fit_summary: str
    missing_skills: list[str]
    missing_keywords: list[str]
    suggested_certifications: list[str]
    suggested_projects: list[str]
    emphasize: list[str]
    confidence: Literal["low", "medium", "high"]

class ReorganizationMove(BaseModel):
    action: Literal["move", "cut", "promote"]
    subject: str           # which section / bullet
    rationale: str

class Reorganization(BaseModel):
    section_order: list[str]
    moves: list[ReorganizationMove]

# Mirrors tailoring's TailoredSuggestion so Fidelity Reviewer + the existing
# _render_one_bullet renderer work unchanged.
class RewriteSuggestion(BaseModel):
    section_label: str
    original_text: str
    suggested_text: str
    claim_type: Literal["restate", "reorder", "quantify", "reframe"]
    supporting_evidence: str   # required, min_length=1

class ResumeClinicReview(BaseModel):
    quality: ResumeQuality
    alignment: Alignment | None         # null when no target given
    reorganization: Reorganization
    rewrites: list[RewriteSuggestion]
```

### Tests

- `test_resume_reviewer_calls_provider_with_correct_agent_name_and_schema`
- `test_resume_reviewer_returns_validated_schema_object_not_dict`
- `test_resume_reviewer_passes_parsed_profile_not_raw_text`
- `test_resume_reviewer_emits_alignment_as_none_when_no_target`
- `test_resume_reviewer_schema_rejects_unknown_quality_dimension`
- `test_resume_reviewer_schema_rejects_empty_supporting_evidence`
- `test_resume_reviewer_registered_in_config_yaml`
- `test_model_pins_includes_resume_reviewer` (pin invariant catches this)

### Deviations from ADR-066

- **Pin discipline applied to a new agent.** Not in the ADR; it's the same
  discipline we just shipped today (commit `e31cee1`). Adding `resume_reviewer`
  to `tests/model_pins.json` so a swap of its model can't land silently.
- **Quality dimensions encoded as `Literal`.** ADR listed the dimensions in
  prose; the schema enforces the enum so a drifted model emitting a free-text
  dimension name fails validation rather than passing through.

### Commit

`feat(resume-clinic): Phase 2 — ResumeReviewerAgent, schema, and prompt`

---

## Phase 3 — Out-of-graph runner + RoleDataProvider seam

**Goal:** `run_clinic()` ties together resume load, role-data lookup (stubbed),
reviewer, fidelity, and persist. No graph entry. Cost attributed via a
lightweight `workflow_runs` row.

### Files

**New**
- `app/services/resume_clinic_runner.py` — `run_clinic(...)`.
- `app/services/role_data/__init__.py` — package marker.
- `app/services/role_data/base.py` — `RoleData`, `RoleDataProvider`,
  `NullRoleDataProvider`.
- `tests/v2/test_resume_clinic_runner.py` — runner tests.

**Modified**
- `app/api/dependencies.py` — wire `ResumeClinicRepository`,
  `ResumeReviewerAgent`, `NullRoleDataProvider`, and a `run_clinic_fn` callable
  into both real and mocked dep graphs.

### Signatures

```python
class RoleData(BaseModel):
    occupation_title: str
    required_skills: list[str]
    tools: list[str]
    certifications: list[str]
    source: str           # "esco" | "onet" | etc.; "null" for the stub

class RoleDataProvider(Protocol):
    def lookup(self, role: str | None, track: str | None) -> RoleData | None: ...

class NullRoleDataProvider:
    """v1 default: returns None for any input. ESCO/O*NET providers are fast-follow."""
    def lookup(self, role, track): return None

def run_clinic(
    user_id: str,
    resume_id: str,
    *,
    target_role: str | None,
    target_track: str | None,
    seniority_aware: bool,
    resume_repo: ResumeRepository,
    clinic_repo: ResumeClinicRepository,
    workflow_repo: WorkflowRepository,
    reviewer: ResumeReviewerAgent,
    fidelity: FidelityReviewer,
    role_data: RoleDataProvider,
) -> dict:  # returns the created clinic_review row
    ...
```

### Runner internals (sketch)

1. Load resume via `resume_repo.get_by_id`; raise `KeyError` if unknown or not
   owned by `user_id`.
2. Generate `clinic_id` and `workflow_run_id` (UUIDs).
3. Write a stub workflow_runs row: `workflow_type="resume_clinic"`,
   `status="running"`, `user_id=user_id`, minimal `state_json`.
4. `role = role_data.lookup(target_role, target_track)` — `None` in v1.
5. Build reviewer context: `{ "_cached": {"resume_profile": parsed},
   "target_role": ..., "target_track": ..., "seniority_aware": bool,
   "role_data": role.model_dump() if role else None }`. raw_text is **not** in
   the reviewer context; it goes to fidelity only.
6. `review = reviewer.run(workflow_run_id, context)`.
7. `fidelity_verdict = fidelity.run(workflow_run_id, {"raw_text": resume.raw_text,
   "rewrites": [r.model_dump() for r in review.rewrites]})`.
8. `clinic_repo.create(...)` — persist all four JSON blobs.
9. Update workflow_runs row to `status="completed"`, `completed_at=now`.
10. Return `clinic_repo.get_by_id(clinic_id)` as a dict.

### Tests

- `test_run_clinic_loads_resume_and_persists_review`
- `test_run_clinic_writes_lightweight_workflow_runs_row`
- `test_run_clinic_always_runs_fidelity_on_rewrites` (the invariant)
- `test_run_clinic_passes_none_to_role_lookup_when_no_target`
- `test_run_clinic_with_no_target_persists_null_alignment`
- `test_run_clinic_raises_on_unknown_resume`
- `test_run_clinic_raises_on_resume_owned_by_different_user`
- `test_run_clinic_null_role_provider_proceeds_and_omits_role_data_block`
- `test_run_clinic_workflow_run_id_attributable_to_user` (cost-attribution invariant)

### Deviations from ADR-066

- **Pinning the `RoleData` shape in v1.** The ADR called the provider seam
  pluggable; making the data shape concrete now lets the Phase-2 prompt and
  schema be designed around a known interface. ESCO/O*NET providers populate
  the same shape later, fast-follow.
- **Reviewer does not receive `raw_text`.** Per CLAUDE.md's prompt rule.
  Fidelity is the only consumer of `raw_text`.

### Commit

`feat(resume-clinic): Phase 3 — out-of-graph runner + RoleDataProvider seam`

---

## Phase 4 — API endpoints

**Goal:** REST surface for running the clinic, listing past runs, and recording
decisions.

### Files

**New**
- `app/api/routers/resume_clinic.py` — router with the three endpoints.
- `tests/v2/test_resume_clinic_router.py` — endpoint tests using the FastAPI
  TestClient + the mocked dep graph.

**Modified**
- `app/api/main.py` — register the router.
- `app/api/dependencies.py` — wiring for the router (already touched in
  Phase 3 for `run_clinic_fn`).

### Endpoints

```
POST   /users/{user_id}/resume-clinic
GET    /users/{user_id}/resume-clinic
POST   /resume-clinic/{review_id}/decisions
```

**`POST /users/{user_id}/resume-clinic`**
- Body: `{resume_id?: str, target_role?: str,
  target_track?: "ic"|"architect"|"management", seniority_aware?: bool=false}`
- Identity: `get_current_user_id()` must equal `{user_id}` (ADR-062 seam).
- If `resume_id` omitted, the user's active resume is used.
- Calls `run_clinic_fn(...)`.
- Returns the persisted clinic-review row.

**`GET /users/{user_id}/resume-clinic`**
- Returns `clinic_repo.list_by_user(user_id)`.

**`POST /resume-clinic/{review_id}/decisions`**
- Body: `{decision: "approve"|"revise"|"reject"|"edit", edited?: dict}`
- Validation reused from the tailoring router's decision validator (extract to
  `app/api/decision_validation.py` so both routers share it).
- `edit` requires a non-empty `edited` payload.

### Tests

- `test_post_clinic_success_with_target`
- `test_post_clinic_success_quality_only_when_no_target`
- `test_post_clinic_uses_active_resume_when_resume_id_omitted`
- `test_post_clinic_404_unknown_user`
- `test_post_clinic_404_unknown_resume`
- `test_post_clinic_403_user_mismatch` (active profile differs from path)
- `test_get_clinic_lists_user_runs_newest_first`
- `test_post_decision_approve_records_state`
- `test_post_decision_edit_requires_edited_payload`
- `test_post_decision_unknown_review_404`
- `test_post_decision_invalid_value_422`

### Deviations from ADR-066

- **`GET /users/{user_id}/resume-clinic` added.** ADR proposed POST + decisions
  only; the GET is implied by Phase 5's "past runs" UI. Trivial.
- **Decision validator extracted.** Cleaner than duplicating tailoring's
  validation. Refactor is contained and the moved function gets a regression
  test against the existing tailoring router.

### Commit

`feat(resume-clinic): Phase 4 — REST endpoints`

---

## Phase 5 — UI

**Goal:** new sidebar view that lets the active profile run the clinic and
review past runs. Reuses the tailoring renderer for the rewrites.

### Files

**New**
- `app/ui/views/resume_clinic.py` — the page.

**Modified**
- `app/ui/streamlit_app.py` — sidebar entry + route to the new view.
- `app/ui/api_client.py` — three new helpers (`post_resume_clinic`,
  `get_resume_clinic_runs`, `post_resume_clinic_decision`).
- `app/ui/db_reader.py` — already touched in Phase 1.

### Layout

```
Active profile: {name}
─────────────────────────────────
Resume:         [picker, defaults to active]
Target role:    [text input; prefilled from profile.search_criteria.roles[0]]
Target track:   ( ) IC  ( ) Architect  ( ) Management  ( ) None
Seniority-aware advice:  [toggle]
                       [Run clinic]

─── Results (after run) ────────────────
Quality scorecard
  • Structure / ordering          [strong | adequate | needs_work]
    findings: ...
    fixes:    ...
  ...
Role / track alignment   (when target given)
  Fit summary: ...
  Missing skills:        [chips]
  Missing keywords:      [chips]
  Emphasize:             [bullets]
  Suggested certifications, projects

Reorganization
  Section order:         [drag-style list]
  Moves:                 [action + rationale per move]

Rewrites
  [_render_tailored_sections renders these]
  [_render_one_bullet renders each bullet with claim_type chip]

Decision: [Approve] [Edit] [Reject]   (tailoring decision controls reused)

─── Past clinic runs ───────────────────
[table — created_at, target_role/track, decision, link to expand]
```

### Tests

- UI tests are manual in this repo (Streamlit is not unit-tested).
- I will start the dev server, run a full clinic end-to-end with the mocked dep
  graph (no API key set), inspect each panel, exercise approve / edit / reject,
  and screenshot anything surprising.
- I'll explicitly call out that I cannot verify the LLM outputs make sense
  without an API key — the notebook in Phase 6 is the surface for that.

### Deviations from ADR-066

- None substantive.

### Commit

`feat(resume-clinic): Phase 5 — Streamlit UI`

---

## Phase 6 — Tests + docs + E2E notebook

**Goal:** close documentation gaps and ship the live-validation notebook.

### Files

**New**
- `notebooks/resume_clinic_validation.ipynb` — live-agent E2E walkthrough.

**Modified**
- `docs/architecture/data_model.md` — add `resume_clinic_reviews` (19 -> 20
  tables); update per-table usage.
- `docs/architecture/api_reference.md` — add the three clinic endpoints with
  status codes and error envelopes.
- `docs/architecture/agent_model.md` — add Resume Reviewer's input/output
  contract entry.
- `docs/architecture/ui_model.md` — add the Resume Clinic view (only if the
  doc exists — I'll check at start of phase).
- `CLAUDE.md` — add Resume Reviewer to the agents table; add clinic
  invariants under existing rules (clinic is out-of-graph; rewrites are
  fidelity-checked; pin invariant covers `resume_reviewer`).
- `docs/wiki.md` — add a section / table row referencing the new feature (if
  applicable; I'll inspect first).
- `docs/user_guide.md` — user-facing how-to (only if exists; check first).
- `CHANGELOG.md` — entry for the clinic feature with ADR-066 reference.

### Notebook structure

1. Preflight: `ANTHROPIC_API_KEY` present; backend running on default port.
2. Resolve the active profile (default user 0); pick the active resume.
3. Set optional `target_role` and `target_track` (notebook cells let you edit).
4. POST `/users/{user_id}/resume-clinic`; capture the response JSON.
5. Render the quality scorecard as a pandas DataFrame (one row per dimension).
6. Render the alignment block if present (markdown + chips).
7. Side-by-side table for rewrites: `original_text` vs `suggested_text` with
   the `claim_type` and `supporting_evidence` columns.
8. Show fidelity verdict (pass/revise/reject).
9. Exercise an `approve` decision (POST `/resume-clinic/{id}/decisions`).
10. Exercise an `edit` decision with a hand-crafted draft.
11. GET past clinic runs for the user; show the list.

### Tests

- Sweep for any gaps from earlier phases.
- Add a single test asserting the `_EXPECTED_TABLES` count matches the
  documented count in `data_model.md`. (Lightweight invariant against doc/code
  drift — same shape lesson as the ADR-index work we did earlier today.)

### Deviations from ADR-066

- **Added a tables-count invariant test.** Not in ADR-066; it's a doc/code
  cross-layer invariant. Cheap, catches a real drift class we already paid for.

### Commit

`feat(resume-clinic): Phase 6 — tests, docs, and live-validation notebook`

---

## Summary of all deviations from ADR-066

These are the points where the implementation walked one step past the ADR's
spec. Each is small enough not to need an ADR amendment, but is named here so
nothing is silently introduced.

| Phase | Deviation | Why |
|---|---|---|
| 1 | `workflow_run_id` column added | needed for cost attribution join |
| 2 | `resume_reviewer` added to `tests/model_pins.json` | extends today's pin discipline |
| 2 | Quality dimensions encoded as `Literal` | hard-rejects schema drift |
| 3 | `RoleData` shape locked in v1 | the prompt has to be designed around it |
| 4 | `GET /users/{id}/resume-clinic` added | Phase 5 UI needs it |
| 4 | Decision validator extracted to a shared module | dedup with tailoring router |
| 6 | tables-count invariant test added | catches doc/code drift |

## Open questions before Phase 1 starts

- **Default model for `resume_reviewer`.** Plan picks `claude-sonnet-4-6`
  (matches `tailoring_agent`, `career_advisor`, `interview_coach`). Confirm or
  override.
- **Whether to extract the decision validator now or keep duplicating.** Plan
  extracts in Phase 4. Confirm or "duplicate, dedupe later."
- **`ui_model.md` and `user_guide.md` existence.** Plan checks at the start of
  Phase 6 and only updates if present. Confirm that approach.
