# ADR-059: Retire the dead in-graph HITL subsystem; add a human `edit` decision to the live tailoring path

## Status

Accepted. Supersedes ADR-011 (Human-in-the-Loop as Backend Workflow Pauses)
in full and resolves the open question ADR-055 left behind ("the in-graph
node still has an unused `user_requested_tailoring` gate that should be
removed if we ever decide the in-graph path is dead code"). The out-of-graph
decision surface of ADR-055 stands and is extended here.

## Context

The system has accumulated a complete in-graph human-in-the-loop (HITL)
subsystem that no live path reaches:

- **Job-selection HITL** was removed when auto-selection shipped. The
  `await_job_selection` node auto-selects qualifying jobs and never calls
  `interrupt()`, so nothing ever emits the `select_jobs_for_deep_review`
  decision the API still accepts.
- **Tailoring-approval HITL** moved out of the graph in ADR-055. The live
  path is `POST /workflows/{wf}/jobs/{job}/tailorings` (the out-of-graph
  router), which calls the agents directly and records the decision in a
  `tailored_resumes` column. The in-graph `tailoring` node and the
  `await_tailoring_approval` interrupt are only reachable when
  `user_requested_tailoring` is `True`, and that flag is initialized to
  `False` and never set by the API or UI.

The result is a dead subsystem spanning the graph, the state schema, and the
API surface:

- `nodes/await_tailoring_approval.py` (the `interrupt()` node)
- `nodes/tailoring.py` (`make_tailoring_node`, the in-graph generator —
  distinct from the out-of-graph router, which does not use it)
- `tailoring_router` + the `tailoring_check_node` shim in the graph
- `graph_state.py`: `pending_decision`, `human_decisions`,
  `user_requested_tailoring`
- `workflows.py`: `POST /{id}/decisions`, `_decision_adapter`, the
  interrupt-detection branch in `submit_workflow`, the retry interrupt-guard
- `schemas/requests.py`: `JobSelectionDecision`, `TailoringDecision`,
  `DecisionRequest`

Separately, the live decision model is narrower than the field's canonical
one. The HITL literature (LangChain's middleware, Anthropic's checkpoint
guidance) treats four decision types as standard: approve, **edit**, reject,
respond. The live path offers approve / revise / reject. "Revise" bounces the
draft back to the agent; there is no way for the human to directly edit a
bullet and save it as final. That gap cuts against the system's own
accountability stance: the human can veto but cannot author.

Two design questions had to be settled:

1. Is interrupt-before (a paused graph) ever the right pattern for this
   system? The only artifact a human gates is a tailored-resume draft, which
   is reversible: nothing consumes it until the human approves. Best practice
   reserves interrupt-before for irreversible, side-effecting actions
   (sending an email, running a migration, submitting an application). This
   system has no such action — application submission is deliberately out of
   scope. Review-after (curate-after) is the correct shape for a reversible
   artifact, and it is what the live path already does.
2. When a human edits a claim directly, who is accountable for it, and does
   the Fidelity Reviewer re-check it? The Fidelity Reviewer exists to police
   the *generator*. A human edit is authored by the person who bears the
   consequence of the resume. Re-reviewing a human's own words would be the
   guardrail second-guessing the accountable party, not the agent.

## Decision

### 1. Retire the dead in-graph HITL subsystem

Remove the in-graph tailoring generator, the `await_tailoring_approval`
interrupt, the routing shim, the dead state fields, and the API decision
surface that fed them (enumerated in Context). After this change the graph
runs end to end with no `interrupt()` anywhere, and `interview_prep` (which
is still reached on score threshold) flows directly to `generate_report`.

The system keeps exactly one HITL pattern: out-of-graph curate-after on
tailoring (ADR-055), plus deliberate no-gate auto-selection upstream. This
is not a reduction in oversight; it is the removal of a second mechanism that
never ran and that the artifact's reversibility does not require.

If a future feature introduces an irreversible action (e.g. an
agent that submits an application on the user's behalf), interrupt-before is
the right pattern for it and should be reintroduced then, scoped to that
action. LangGraph's `interrupt()` / `Command(resume=...)` remains the
intended primitive; this ADR does not ban it, it removes an unused instance.

### 2. Add a human `edit` decision to the live tailoring path

Extend the out-of-graph decision model from {approve, revise, reject} to
{approve, revise, reject, **edit**}:

- `edit` carries the human-authored final draft in the request body. It is an
  acceptance with modifications: `approved` flips to `1`, `decision="edit"`.
- The edited draft is persisted alongside the agent's original draft, never
  overwriting it. The original agent draft and its FidelityReview are
  retained for the audit trail.
- A human edit is **trusted as final and is not re-run through the Fidelity
  Reviewer.** The reviewer polices the generator, not the accountable human.
  The persisted record marks the final text as human-authored so the audit
  shows who wrote the claim.

`approved` is now derived as `1` when `decision in {approve, edit}`, else `0`.

### 3. Clarify the evidence-binding invariant

"Every tailored claim must include `supporting_evidence` from the original
resume" applies to **agent-authored** claims — it is a constraint on the
generator, enforced at the `TailoredBullet` schema boundary. A human `edit`
is owner-authored and is not subject to the evidence schema: the person who
owns the resume is the accountable author of their own words. This is a
clarification, not a relaxation; the agent path is unchanged.

## Rationale

- **Dead code is a tax.** The in-graph subsystem carries maintenance,
  test, and comprehension cost (it makes the graph read as if tailoring runs
  inside it, which it does not) for zero live behavior.
- **Pattern fit over pattern count.** Curate-after is the correct HITL shape
  for a reversible artifact. Keeping interrupt-before "in case" is
  speculative generality; reintroducing it when a genuinely irreversible
  action appears is straightforward.
- **Accountability requires authorship.** Letting the human edit and own the
  final text is the strongest form of "the last word is never the model's."
  A veto-only model leaves the human accountable for words they could not
  change.
- **The guardrail polices the agent, not the human.** Re-reviewing a human
  edit would invert the trust relationship the Fidelity Reviewer exists to
  encode.

## Consequences

### Positive

- The workflow graph has a single, honest control flow with no unreachable
  branches and no `interrupt()` machinery to reason about.
- The API surface shrinks by one endpoint and one request union; the state
  schema sheds three fields.
- The live decision model matches the canonical approve / edit / reject /
  respond shape (respond is N/A here — there is no "ask the user" tool), with
  the human as final author.
- The audit trail gains a clear agent-authored vs human-authored distinction.

### Tradeoffs

- The system can no longer pause a running workflow for a human decision
  without reintroducing the primitive. Accepted: no current action needs it,
  and the reintroduction path is well understood.
- `tailored_resumes` gains one column (`edited_json`) and the decision
  vocabulary grows by one value. Migration is a try/except `ALTER TABLE` in
  `init_db()`, safe on existing databases (same pattern as ADR-055/ADR-057).
- Older checkpoints written with `pending_decision` / `human_decisions` keys
  remain readable (TypedDict `total=False`); the keys are simply no longer
  produced or consumed.

### Neutral

- `db_reader.py` must mirror the new column (per the persistence rule that DB
  schema changes update both the repository layer and the UI read path).
- The blog series' Article 8 slot ("two HITL patterns, one app") is reframed
  to reflect the system as it now is: one HITL pattern chosen on purpose, one
  retired with rationale.

## Implementation Notes

Retire (Decision 1):
- `app/workflows/workflow_graph.py` — drop the `tailoring`,
  `await_tailoring_approval`, and `tailoring_check_node` nodes and their
  edges; route `interview_prep -> generate_report`; drop the
  `interview_router` "tailoring_check" branch (returns `generate_report`).
- Delete `app/workflows/nodes/await_tailoring_approval.py` and
  `app/workflows/nodes/tailoring.py`.
- `app/workflows/routers.py` — remove `tailoring_router`; simplify
  `interview_router` to route to `generate_report` instead of
  `tailoring_check`.
- `app/workflows/graph_state.py` — remove `pending_decision`,
  `human_decisions`, `user_requested_tailoring`.
- `app/api/routers/workflows.py` — remove `POST /{id}/decisions`,
  `_decision_adapter`, the `submit_workflow` interrupt branch, and the retry
  interrupt-guard; drop the `user_requested_tailoring` / `pending_decision` /
  `human_decisions` initializers.
- `app/api/schemas/requests.py` — remove `JobSelectionDecision`,
  `TailoringDecision`, `DecisionRequest`.
- Tests: prune interrupt/decision cases from `test_workflow_nodes.py`,
  `test_api_workflows.py`, `test_workflow_graph.py`.

Add `edit` (Decision 2):
- `app/api/routers/tailoring.py` — `TailoringDecisionRequest.approval` gains
  `"edit"`; add an optional `edited` field (the human draft) required when
  `approval == "edit"`.
- `app/repositories/tailoring_repository.py` — `set_decision` accepts the
  edited draft; `approved = 1 if decision in {"approve", "edit"} else 0`;
  persist `edited_json`.
- `app/repositories/database.py` — add `edited_json TEXT` column +
  `ALTER TABLE` migration.
- `app/api/schemas/responses.py` — `TailoringResponse` surfaces `edited` and
  the authored-by distinction.
- `app/ui/db_reader.py` — read `edited_json`.
- `app/ui/streamlit_app.py` — add an inline edit affordance to the tailoring
  card; "Save edited" records an `edit` decision.
- Tests: extend `test_tailoring_router.py` and `test_repositories.py` for the
  edit path, validation (edited body required on edit), and the `approved`
  derivation.

Docs to update after implementation: `CLAUDE.md` (HITL rules),
`docs/architecture/hitl.md`, `workflow_model.md`, `api_reference.md`,
`state_and_memory_model.md`, and the ADR index.

## References

- ADR-011 — Human-in-the-Loop as Backend Workflow Pauses (superseded).
- ADR-055 — On-Demand Tailoring as an Out-of-Graph Operation (extended).
- ADR-015 — Tailoring Must Be Evidence-Bound (clarified, Decision 3).
- ADR-016 — Add Fidelity Reviewer After Tailoring Agent (scope clarified).
- LangChain, Human-in-the-loop docs — the canonical approve / edit / reject /
  respond decision model.
- Anthropic, Building Effective Agents — checkpoints reserved for high-stakes,
  irreversible actions.
