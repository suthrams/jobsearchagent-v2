# ADR-083: Cooperative Workflow Run Cancellation

## Status

Accepted (2026-06-04). Implemented.

Companion to ADR-082 (idempotent kickoff). Both are run-lifecycle controls over
HTTP and share `app/workflows/run_control.py`.

## Context

A workflow run can take tens of seconds to minutes and spends real money as it
goes (LLM calls per node). Today there is no way to stop one. A user who realizes
mid-run that they started the wrong run, picked the wrong resume, or set the wrong
search has only two options: wait for it to finish (and pay for it) or restart the
server (which kills *all* in-flight runs and corrupts checkpoints).

This is again an agentic-vs-enterprise difference: a long-running, money-spending
compute job needs a kill switch. The system already has an *automatic* shed valve
(`MAX_LLM_CALLS_PER_RUN` -> `budget_cap_reached`, ADR-076); it lacks a *manual*
one.

Constraints from the existing architecture:

- The graph runs via `graph.invoke()` in a thread pool inside the API process.
  An in-flight `invoke()` holds its channel state in memory and passes it
  node-to-node; it does **not** re-read the checkpoint between nodes. So an
  external `update_state` write cannot be "seen" mid-invoke. Cancellation must be
  cooperative and checked from inside the run.
- Every node is already wrapped by `_instrument_step` (ADR-074 Gap 2). That wrapper
  is the natural, single place to check a cancel flag at each node boundary.

## Decision

Add a cooperative cancellation mechanism keyed on `workflow_id`, checked at node
boundaries.

### A. `POST /workflows/{workflow_id}/cancel`

- `404 workflow_not_found` if the thread has no checkpoint.
- `409 workflow_not_cancellable` if the run has no pending steps (`snapshot.next`
  is empty) - it is already terminal (completed / failed / cancelled) or parked
  (`awaiting_scoring_selection`, which is not consuming anything).
- Otherwise: record the request in a process-level `CancellationRegistry`
  (lock-protected set in `app/workflows/run_control.py`), best-effort write
  `status="cancelling"` to the checkpoint, and return `202`
  `{workflow_id, status: "cancelling"}`. **Cancel is idempotent** - re-cancelling a
  cancelling run returns `202` again.

### B. Node-boundary check in `_instrument_step`

Before a wrapped node runs, `_instrument_step` checks
`is_cancel_requested(workflow_id)`. If set, it raises `WorkflowCancelled` *before*
logging a `step_started` row (a cancelled-before-it-ran node is not a failed step).
This makes the cancel take effect at the **next node boundary**. A node already
executing (for example `score_jobs` scoring a batch concurrently) finishes first;
cancellation granularity is one node, not one LLM call. This is a deliberate,
documented limit - node boundaries are the cheap, safe checkpoint.

### C. Finalization

`_run_graph` / `_retry_graph` catch `WorkflowCancelled`, write the terminal
`status="cancelled"` to the checkpoint (safe: the `invoke()` has unwound, so no
node is concurrently writing), and clear the registry flag in a `finally`. The
terminal write is authoritative; the interim `cancelling` is display only.

### D. Status surfacing (`_read_status`)

`GET /workflows/{id}` previously derived status purely from the
`snapshot.next -> "running"` heuristic, which would mask an explicit terminal
status. The precedence is now:

1. explicit terminal `state["status"]` in `{cancelled, failed, completed}` wins;
2. else if the cancel registry has the id -> `cancelling`;
3. else `snapshot.next` non-empty -> `running`;
4. else the stored status (or `completed`).

This makes cancellation visible immediately and race-free, and as a side benefit
makes the pre-existing `failed` write actually surface (it was previously masked by
a non-empty `snapshot.next`).

New status values: `cancelling` (transient) and `cancelled` (terminal). The
`workflow_runs.update_state` SQL already treated `cancelled` as a terminal status
that stamps `completed_at`, so no schema change is needed.

The Streamlit Live Run Monitor gains a Cancel control while a run is
running / cancelling.

## Options considered

- **Cooperative node-boundary cancel (chosen).** Rides the existing
  `_instrument_step` seam; no new state key (avoids the LangGraph TypedDict
  key-drop landmine); authoritative via an in-process registry that the running
  thread can read. Simple and correct for the single-process deployment.
- **External `update_state` flag the nodes read (rejected).** An in-flight
  `invoke()` does not re-read the checkpoint between nodes, so the running graph
  would not see the flag. Would not work without re-architecting the run loop.
- **Hard thread kill (rejected).** Python cannot safely kill a thread; it would
  leave half-written checkpoints and DB rows. Cooperative is the safe option.
- **Per-LLM-call cancellation (rejected for now).** Finer granularity, but it would
  thread a cancel check through every agent call site. Node-boundary granularity is
  enough to stop the spend quickly and is far less invasive.

## Consequences

### Positive

- A run can be stopped on demand; the spend stops at the next node boundary instead
  of running to completion.
- Reuses the `_instrument_step` wrapper and the `run_control` module from ADR-082;
  no new state-channel key, no schema change.
- `_read_status` is now honest about terminal states (fixes the masked-`failed`
  latent issue).

### Tradeoffs / limits (honest)

- Granularity is one node. A long node (a concurrent scoring batch) completes
  before the cancel lands; the user is told "cancellation takes effect at the next
  step."
- The registry is process-local (same limit as ADR-082's guard). A multi-worker
  deployment would need shared state.
- A server restart loses the in-memory registry; but it also loses the in-flight
  thread-pool runs, so there is nothing to cancel after a restart (the run is
  already dead and recoverable via `retry`).

### Neutral

- Docs (architecture-docs sweep mandate): this ADR + index, `api_reference.md`
  (the cancel endpoint, `cancelling` / `cancelled` status values, the
  `workflow_not_cancellable` error), `api_surface_overview.md` (endpoint + count),
  `data_model.md` (the cancelled terminal status note), `workflow_model.md`
  (cancellation control flow), CLAUDE.md (orchestration rules), CHANGELOG. Tests:
  `CancellationRegistry` unit tests; `_instrument_step` raises on a requested
  cancel; cancel endpoint 404 / 409 / 202; `_read_status` reports
  cancelling / cancelled.

## References

- ADR-082 — idempotent kickoff + in-flight guard (shares `run_control.py`).
- ADR-074 — the `_instrument_step` node wrapper this hooks for the cancel check.
- ADR-076 — the automatic budget-cap shed valve (the cost analog of a manual
  cancel).
