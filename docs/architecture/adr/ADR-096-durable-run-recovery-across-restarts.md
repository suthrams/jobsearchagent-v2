# ADR-096: Durable Run Recovery Across Process Restarts (Graceful Drain + Checkpointed Auto-Resume)

## Status

Accepted (2026-06-09). Implemented.

Companion to ADR-082 (idempotent kickoff + in-flight guard) and ADR-083
(cooperative cancellation). All three are run-lifecycle controls; this one closes
the gap they explicitly left open: a process restart kills in-flight runs.

## Context

A workflow run executes via `graph.invoke()` on a background thread in a
module-level `ThreadPoolExecutor` **inside the API process** (`_executor` in
`app/api/routers/workflows.py`). `POST /workflows` returns `202` immediately and
the run continues for tens of seconds to minutes, detached from the request.

Only two nodes write the `workflow_runs` row: `register_run` (initial) and
`generate_report` (terminal). Everything in between lives in the LangGraph
checkpointer (real mode = **SqliteSaver**, durable) and the observability tables.

This produced an observed failure. A run was executing `career_advice` when the
uvicorn worker process was torn down (the dev driver was `uvicorn --reload`, which
kills and respawns the worker on any file change; a manual restart or a deploy does
the same). During interpreter teardown CPython runs the `atexit` hook
`concurrent.futures.thread._python_exit()`, which sets a module-global
`_shutdown = True` and joins the pool's worker threads. The still-running graph
thread then tried to schedule work (a nested `ThreadPoolExecutor` in
`score_jobs`/`deep_review`, or thread-backed work in the LLM client) and hit the
guard, raising:

```
RuntimeError: cannot schedule new futures after interpreter shutdown
```

The same class of error had previously hit `interview_coach` and
`fidelity_reviewer` — whichever node happened to be running at shutdown. It is not
an agent bug; it is a **process-lifecycle race**: execution lifetime is tied to
web-server lifetime, with no durability handoff at shutdown.

The symptom the user sees: the `workflow_runs` row freezes at `running` (neither
terminal node ran), so the UI shows the run as perpetually running. ADR-083 already
noted this ("a server restart ... loses the in-flight thread-pool runs ... the run
is already dead and recoverable via `retry`") — recovery was **manual** (the Live
Monitor Retry button, which calls `_retry_graph -> graph.invoke(None, config)` to
resume from the checkpoint). This ADR makes recovery **automatic**.

Constraints carried from the existing architecture (and project policy):

- **No Redis / Celery / external queue** (CLAUDE.md "Explicitly excluded"). The fix
  must stay in-process and use the SQLite-backed checkpointer already present.
- The checkpointer is durable in real mode (SqliteSaver) and `graph.invoke(None,
  config)` already resumes a thread from its last checkpoint — the resume primitive
  exists; it just was not wired to startup.
- The deployment is single-process (one uvicorn worker; the process-local
  `run_control` registry from ADR-082/083 already assumes this).

A separate worker process / job queue is the textbook fix (it decouples execution
from the web lifecycle), but it is out of scope by the no-Redis/Celery policy. The
strongest fix achievable within the constraints is to make a restart **pause** a
run rather than **kill** it.

## Decision

A two-layer durability stack around the existing executor + checkpointer, plus the
already-shipped reconciliation as the backstop.

### Layer 1 — Graceful drain on shutdown

Track every submitted run future. On the FastAPI lifespan **shutdown** (before
interpreter teardown begins), call `drain_inflight_runs(timeout)`:

- `concurrent.futures.wait(pending, timeout=...)` gives in-flight runs a bounded
  window to reach their next node boundary and checkpoint cleanly.
- Because this runs **before** the `atexit` `_shutdown` flag is set, a run that
  finishes within the window never hits the "cannot schedule new futures" race.
- The window is bounded (default 30s, env `WORKFLOW_SHUTDOWN_DRAIN_SECONDS`) so a
  slow run cannot hang shutdown indefinitely. A run that exceeds it is left to
  Layer 2 on the next boot — correctness does not depend on the drain finishing.
- The pool is **not** explicitly shut down: at real process exit it dies with the
  process, no new work arrives during shutdown (the server has stopped accepting
  connections), and an explicit `shutdown()` would permanently disable the
  module-level executor — fatal when a test harness re-enters the lifespan in one
  process. A bounded `wait` is sufficient.

The new submit seam is `_submit_run(...)`, which wraps `_executor.submit(...)` and
registers the future (with a done-callback to deregister). All three kickoff sites
(`POST /workflows`, `/retry`, phase-2 `/scoring`) and the Layer-2 resume go through
it.

### Layer 2 — Guarded checkpointed auto-resume on startup

On the FastAPI lifespan **startup**, after the graph is built, call
`recover_orphaned_runs(graph)`:

- At startup the `_executor` and the `run_control` registry are freshly empty, so
  any `workflow_runs` row still at `running` / `cancelling` is **definitively
  orphaned** (its owning process is gone).
- For each orphan **under the resume-attempt cap**: bump its
  `state.resume_attempts`, then `_submit_run(_retry_graph, graph, config)` — which
  resumes from the SqliteSaver checkpoint at the next pending node. No work already
  done is repeated; the run continues to `generate_report`.
- For each orphan **at/over the cap** (`MAX_RESUME_ATTEMPTS = 3`): mark it `failed`.
  The cap guards against a *poison run* that crashes the process on every resume,
  which would otherwise produce a restart loop. The counter lives in the run's own
  `state_json` (`resume_attempts`), so no schema change.

### Layer 3 — Reconciliation backstop (already shipped earlier this session)

`WorkflowRepository.reconcile_orphaned_runs()` flips orphaned rows to `failed`
(column + embedded `state.status` + `completed_at` + `error_message`,
`error_type="ProcessInterrupted"`). It is now expressed in terms of the per-run
`mark_failed()` that Layer 2 also uses, and remains the fail-everything fallback
(e.g. when no durable resume is possible). Terminal and parked
(`awaiting_scoring_selection`) runs are never touched.

Net behavior: a restart at worst pauses a run for a few seconds (drain) or until the
next boot (resume); only runs that exhaust their resume attempts end as `failed`.

## Options considered

- **Graceful drain + checkpointed auto-resume (chosen).** Reuses the durable
  SqliteSaver and the existing `graph.invoke(None, config)` resume primitive; no new
  infrastructure; bounded so it can't hang shutdown or loop forever. Converts a
  restart from "kills the run" to "pauses the run."
- **Drain only (rejected as insufficient).** A bounded drain still kills any run
  longer than the window, and an unclean crash (SIGKILL, OOM) skips lifespan
  shutdown entirely. Without resume, those runs still die. Drain alone is a
  best-case-only fix.
- **Auto-resume only, no drain (rejected as insufficient).** Works, but every
  graceful restart would still emit the "cannot schedule new futures" error and
  briefly thrash a run mid-node before resuming. The drain makes the common case
  clean.
- **Hard unbounded `_executor.shutdown(wait=True)` (rejected).** Could block
  shutdown for minutes on a long run, making `--reload` and deploys unusable.
- **Separate worker process / job queue — Celery/RQ/Arq (rejected by policy).** The
  architecturally correct decoupling, but excluded by the no-Redis/Celery rule. The
  chosen design is the strongest in-process approximation; this remains the upgrade
  path if the constraint is ever lifted.
- **Mark-all-orphans-failed only (the earlier reconciliation, kept as backstop).**
  Honest but lossy — the user must manually re-run. Auto-resume preserves the work
  already paid for.

## Consequences

### Positive

- A process restart (dev `--reload`, deploy, or graceful stop) no longer loses
  in-flight runs: they drain cleanly or auto-resume from their checkpoint.
- The "cannot schedule new futures after interpreter shutdown" failure is eliminated
  for graceful shutdowns and made benign (auto-recovered) for hard ones.
- Reuses the SqliteSaver checkpointer and the `_retry_graph` resume path; no schema
  change (the attempt counter rides `state_json`), no new dependency.
- Orphaned rows can no longer show as perpetually `running`.

### Tradeoffs / limits (honest)

- **Single-process assumption.** Correct for one-worker uvicorn and `--reload`
  (only the killed worker's runs are orphaned). A true multi-worker deploy would
  auto-resume another live worker's runs on startup — it needs a shared run registry
  first (same limit ADR-082/083 carry). Documented, not yet enforced.
- **Resume granularity is one node.** A run resumes from its last *checkpoint*
  (node boundary), so the node that was interrupted re-runs from its start — its
  partial LLM spend is paid twice. Node-boundary granularity matches ADR-083.
- **Drain delays shutdown** by up to the timeout when a run is active; tunable via
  env, default 30s. Set it low for fast dev iteration.
- **Resume assumes a durable checkpointer** (real mode = SqliteSaver). In mocked
  mode (MemorySaver) there is no checkpoint to resume after a restart; the attempt
  cap then fails the run after `MAX_RESUME_ATTEMPTS`. Mocked mode is test-only.

### Neutral

- Docs (architecture-docs sweep): this ADR + the ADR index, `workflow_model.md`
  (status lifecycle + the recovery/drain note), CLAUDE.md (run-lifecycle controls
  invariant), CHANGELOG. Tests: repository `list_orphaned_runs` / `bump_resume_attempt`
  / `mark_failed` / `reconcile_orphaned_runs`; `recover_orphaned_runs` resumes under
  the cap and fails over it; `drain_inflight_runs` waits then stops.

## References

- ADR-082 — idempotent kickoff + in-flight guard (shares `run_control.py`; the
  guard this resume re-acquires).
- ADR-083 — cooperative cancellation; noted the restart-kills-runs gap this closes,
  and the `_retry_graph` resume primitive reused here.
- ADR-047 — SqliteSaver as the checkpoint store that makes resume durable.
- `workflow_model.md` — status lifecycle + startup recovery.
