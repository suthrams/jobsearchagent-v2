# ADR-055: On-Demand Tailoring as an Out-of-Graph Operation

## Status
Accepted

## Context

Resume tailoring (TailoringAgent + FidelityReviewer, ADR-015 / ADR-016) was implemented in Phase 4 as a node inside the LangGraph workflow, gated by `state["user_requested_tailoring"]`. The node sits between `interview_prep` (or `career_advice` skip-path) and `await_tailoring_approval`, which uses LangGraph's `interrupt()` HITL primitive (ADR-011) to pause for user approve / revise / reject.

In practice this path was never reached:

- Nothing in the API or UI ever set `user_requested_tailoring=True`.
- After the workflow completes (`generate_report → END`), the graph cannot be restarted to add a tailoring step — interrupts only work mid-execution.
- Tailoring is fundamentally a per-job operation that the user wants to invoke selectively, after seeing scoring / deep-review / interview-prep output, sometimes for multiple jobs from a single run, sometimes hours or days later.

Three options were considered:

1. **Re-enter the graph.** Add a new entry point that loads the completed workflow's checkpoint, sets `user_requested_tailoring=True`, and resumes. Forces tailoring to run on whatever job the graph picked (`max(selected_jobs, key=overall_score)`); awkward to target a specific job; conflates "the workflow's final state" with "the user's current intent"; reuses an interrupt primitive that doesn't fit a finished run.
2. **Add per-job tailoring nodes inside the graph.** Forces the graph to know up-front which jobs the user will eventually want tailored; turns a user-initiated post-hoc operation into a pre-declared intent; doesn't solve the "tailor again after looking at the result" case.
3. **Expose tailoring as an out-of-graph operation.** Treat tailoring as a stateless service the API can call directly, using the workflow's persisted state for context. Decouples the lifetime of the user's tailoring intent from the lifetime of the graph run.

## Decision

Tailoring is exposed as an out-of-graph operation via a dedicated FastAPI router (`app/api/routers/tailoring.py`):

```
POST /workflows/{workflow_id}/jobs/{job_id}/tailor   → run tailoring + fidelity, persist, return draft
GET  /workflows/{workflow_id}/tailorings             → list drafts for a workflow
GET  /tailorings/{tailoring_id}                      → fetch one draft
POST /tailorings/{tailoring_id}/decision             → record approve / revise / reject
```

The router reads the workflow state from the LangGraph checkpoint (`graph.get_state(config).values`) to source `resume_profile`, `selected_jobs`, etc., and reads per-job critic / advisor output from the relational repos (`review_repo.get_review_by_run_job`, `advice_repo.get_advice_by_run_job`). It then invokes `TailoringAgent.run()` and `FidelityReviewer.run()` directly — same agents the graph would use, same fidelity invariant — and persists the draft to `tailored_resumes` along with the FidelityReview output.

The in-graph tailoring node, the `await_tailoring_approval` interrupt, and the `user_requested_tailoring` flag remain in the codebase. They are kept as the path for users who want tailoring to run automatically as part of a workflow they kick off ahead of time. They are not currently wired to the UI; that integration can be added later without changing this ADR.

## Rationale

- **Lifecycle decoupling.** Workflow completion is a discrete event; tailoring intent isn't. Tying tailoring to the graph forced both to share a lifetime they don't actually share.
- **Per-job targeting.** The router takes `(workflow_id, job_id)` so the user picks the job. The in-graph node always picks the highest-scored job, which is rarely what the user wants when reviewing 5+ qualifying matches.
- **Repeatable.** The user can generate multiple drafts for the same job (e.g. before and after revising their resume) without re-running discovery + scoring.
- **Same fidelity contract.** TailoringAgent and FidelityReviewer are the same agents, with the same prompts and structured outputs (ADR-015, ADR-016). Evidence-binding and the "label as gap, never rewrite as if present" invariant hold identically; only the trigger surface changes.
- **No new HITL primitive.** Approval is recorded in `tailored_resumes.decision` (a column, not a graph interrupt), since there is no graph paused for the decision.

## Consequences

### Positive
- Tailoring is the first feature a user can actually exercise from the UI without arranging it pre-run.
- The new endpoints are testable in isolation (TestClient + dependency overrides — see `tests/v2/test_tailoring_router.py`); no need to drive the full graph to exercise them.
- Repeated tailoring of the same job is cheap (~6 LLM calls per attempt) and produces a clean per-draft history in `tailored_resumes`.

### Tradeoffs
- The router synchronously runs ~6 LLM calls per request (5–15s wall clock). FastAPI can hold the connection that long without issue, but if we ever want concurrent tailoring across many jobs the router will need a thread-pool indirection (same pattern as `app/api/routers/workflows.py::_executor`).
- Two paths (in-graph and out-of-graph) now exist for tailoring. Both must enforce the same fidelity invariants. The in-graph node still has an unused `user_requested_tailoring` gate that should be removed if we ever decide the in-graph path is dead code.
- `tailored_resumes` schema diverges from the original Phase 4 spec: three new columns (`fidelity_review_json`, `decision`, `decided_at`) carry data that previously lived in `human_decisions` rows or wasn't persisted at all. Migration is via try/except `ALTER TABLE` in `init_db()` and is safe for existing databases.

### Neutral
- `app/api/dependencies.py` now exposes `get_deps()` (returns the `WorkflowDependencies` bundle). The new router uses it; other routers continue to use `get_graph()` exclusively.
- `tailored_resumes.approved` (legacy boolean) is now derived from `decision`: it flips to `1` only when `decision="approve"`. Older code reading `approved` continues to work.

## Implementation Notes

- `app/api/routers/tailoring.py` — the new router.
- `app/repositories/tailoring_repository.py` — added `set_decision()`, `list_by_workflow()`, `get_by_id()`; `create()` now accepts an optional `fidelity_review` dict.
- `app/repositories/database.py` — three new columns on `tailored_resumes` plus matching ALTER TABLE migrations in `init_db()`.
- `app/api/dependencies.py` — `get_deps()` returns the singleton `WorkflowDependencies` so routers can inject individual agents without rebuilding the graph.
- `app/workflows/nodes/tailoring.py` — fixed a latent bug where `tailoring_repo.create()` was called with 4 args instead of 5 (missing `resume_id`). Would have crashed the in-graph path the first time it ran.
- `app/ui/streamlit_app.py` — Workflow Detail gains a "Resume Tailoring" section per deep-reviewed job: side-by-side `Original → Suggested` diffs with cited evidence, claim-type / fidelity-risk badges, fidelity-flag panel, approve / revise / reject buttons.
- 9 new tests in `tests/v2/test_tailoring_router.py`.

## References
- ADR-011 — Human-in-the-Loop as Backend Workflow Pauses (now applies only to the in-graph tailoring path; the out-of-graph path uses ordinary REST decision endpoints).
- ADR-015 — Tailoring Must Be Evidence-Bound.
- ADR-016 — Add Fidelity Reviewer After Tailoring Agent.
- ADR-035 — Enforce a Structured Workflow State Schema (state read by the router via `graph.get_state(config).values`).
