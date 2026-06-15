# Persistence and Concurrency

> **Type:** Explanation + reference. **Part of:** [Maintainer Handbook](../maintenance.md).
>
> How the system stores data and runs work concurrently, *and where those two interact
> dangerously.* This doc makes the single-process and best-effort-persistence assumptions
> concrete in code so you can reason about them. The schema mechanics (adding a column,
> migrations) are in [schema_and_migrations.md](schema_and_migrations.md).

---

## Two state stores, two jobs

A workflow run's state lives in **two** places, written separately:

| Store | Table(s) | Purpose | Who reads it |
|---|---|---|---|
| **Domain store** | `workflow_runs` (+ child tables: `job_scores`, `review_rounds`, ...) | The durable record: status, metrics, results. | The UI / history / analytics — **always read here.** |
| **Checkpointer** | `checkpoints` (LangGraph `SqliteSaver`, in `data/v2.db`) | Resumption only — lets a crashed run resume from its last node. | The graph runner and ADR-096 recovery. **Never** the UI. |

> **Rule:** query `workflow_runs` for any UI/history read. The `checkpoints` table is an
> implementation detail of resumption.

**They are not written atomically** (`register_run.py` writes `workflow_runs`; the graph
checkpointer writes `checkpoints` independently). A crash *between* the two can leave the UI
reading `status:"running"` forever while the checkpoint says done. ADR-096 recovery +
`reconcile_orphaned_runs` fix the *restart* case; an un-restarted process leaves a stuck row
(see [troubleshooting](backup_restore_and_troubleshooting.md)). Closing this divergence
atomically is open roadmap item (review §2.5).

---

## SQLite configuration (WAL + busy_timeout)

Every connection is opened through one context manager,
`app/repositories/database.py::get_connection`:

- `sqlite3.connect(..., timeout=15.0)` — the Python-level busy wait.
- `PRAGMA journal_mode=WAL` — Write-Ahead Logging. Readers run **alongside** a single
  writer (no reader/writer blocking). WAL is a persistent per-DB property; re-issuing it
  each connect is an idempotent no-op.
- `PRAGMA busy_timeout=15000` — a contended writer **waits** up to 15s for the lock instead
  of raising `SQLITE_BUSY` immediately.
- commit on clean exit, rollback on exception, always close.

**Why this exists:** the scoring node fans out writes across a thread pool (next section).
Before this hardening (2026-06-13 fix 3), a contended writer could raise `SQLITE_BUSY`, and
that exception was swallowed (fix 1), losing a *paid* score silently. WAL + the busy timeout
make collisions wait rather than fail. This is a `Low-Medium` risk now, `High` if ever run
multi-writer at scale.

> **Operational note:** WAL creates sidecar files (`v2.db-wal`, `v2.db-shm`). They matter
> for backup/restore — see [backup_restore_and_troubleshooting.md](backup_restore_and_troubleshooting.md).

---

## The concurrency model: single process, in-process executor

Everything runs in **one process**. There is no Celery, Redis, or external queue (these are
explicitly excluded from the stack).

- **Workflow execution** runs in a module-level `ThreadPoolExecutor` (`_executor` in
  `app/api/routers/workflows.py`). `POST /workflows` submits a run to it and returns `202`
  immediately; the run executes in a background thread.
- **Scoring** fans out *within* a run: `score_jobs.py` runs research + scoring across a
  `ThreadPoolExecutor` (5 worker threads), each writing scores concurrently — this is the
  one place multiple DB writers are active at once.
- **Run-lifecycle registries** (`app/workflows/run_control.py`) are in-memory sets behind
  locks:
  - `try_acquire_running` / `release_running` — the single-flight guard (ADR-082) that
    blocks concurrent re-submit on `/retry` and `/scoring` (`409 workflow_already_running`).
  - `request_cancel` / `is_cancel_requested` — cooperative cancellation (ADR-083);
    `_instrument_step` checks it at each node boundary and raises `WorkflowCancelled`.
- **Idempotency** (`POST /workflows` with an `Idempotency-Key`) and **run recovery**
  (ADR-096) are likewise in-process.

### Why single-process is load-bearing

All four of the above are **authoritative only because one process holds them.** The
FastAPI handlers and the workflow threads share the same memory, so they see the same
registries.

> **Running `--workers 2` / `WEB_CONCURRENCY>1` breaks all of it:** two workers have
> separate registries, so the single-flight guard, cancellation, idempotency, and recovery
> all bypass across workers -> double-runs and double-spend. As of **ADR-106** the startup
> guard (`app/api/deployment_guard.py`, called first in the lifespan) **detects this and
> refuses to boot** — so the failure is loud, not silent (override with
> `ALLOW_UNSAFE_DEPLOYMENT=1` if you accept the risk). The guard is a best-effort tripwire
> for the common launch commands, **not** a fix: a real multi-worker rollout still needs a
> shared store (a Redis or DB advisory lock). Keep it at one worker, bound to loopback.

---

## Best-effort persistence: where paid output can be lost

This is the most important runtime risk in the *intended* (single-user) context — it needs
neither multi-user nor multi-process to fire.

**The pattern to recognize:** a domain write wrapped in `try/except: log + continue`, with
the caller still returning success. For *discovery filters* this is correct ("never lose the
run" — a filter failure should keep all jobs, not drop the run). For *paid agent output* it
is dangerous: a swallowed write loses the result **and** re-spends LLM cost on the next run,
while telling the user it succeeded.

**What was hardened (2026-06-13 roadmap items 1-3):**

1. **Paid-output persist failures now surface.** Scoring, career advice, deep review, and
   interview prep write failures go to the run's `errors[]` / a `persisted:false` API flag
   instead of being swallowed. (The tailoring-create path was validated as *already* loud —
   it raises a 500 rather than masking the failure.)
2. **`job_scores` is now idempotent.** A `UNIQUE(workflow_run_id, job_id)` constraint +
   `INSERT OR IGNORE` (with a dedupe-safe migration) stops a double-clicked on-demand score
   from inserting duplicate rows and double-spending. Note the process-local `run_control`
   guard covers `/retry` and `/scoring` but historically **not** the single-job score
   endpoint — the DB constraint is the backstop there.
3. **WAL + busy_timeout** (above) reduce the contention that triggered the swallowed writes.

**The rule for new code:**

> When you add an agent or any write that persists **paid** output, a persist failure must
> be **loud** — surface it to `errors[]` / status. Do **not** copy the discovery filters'
> swallow-and-continue. Reserve "never lose the run" for *filters*, never for *results*.

---

## The cost cap is a soft governor, not a hard ceiling

`MAX_LLM_CALLS_PER_RUN` (200) is the absolute backstop, but understand its limits:

- It is read **once** before the 5-thread scoring fan-out (`score_jobs.py`), and the counter
  is updated in-memory **after** all threads finish. Concurrent threads plus a research retry
  can overshoot the pre-flight estimate.
- The conservative `//2` pre-flight estimate and the per-run scope bound the blast radius, so
  in practice overshoot is small — but it is **not** a transactional guarantee.

Treat the cap as a governor that bounds a run, not a hard limit you can rely on to the exact
call. For real cost control, lever the funnel width (`scoring.max_scored`), the model
assignment, and the opt-in filters — see [cost_troubleshooting.md](../cost_troubleshooting.md)
and [model_recommendations.md](../model_recommendations.md).

---

## At-rest data (what is plaintext today)

PII is **unencrypted at rest** by design (ADR-070 Phase 2 encryption is pending):

- `resumes.raw_text` + `resumes.parsed_profile_json` — names, emails, phones.
- `workflow_runs.state_json` — though `load_resume` stores the **redacted** profile here, so
  raw identifiers do not enter workflow state / checkpoints (ADR-070).

The un-redacted profile's only at-rest home is the `resumes` row. This is `Low` risk now
(single-user, loopback), `High` if exposed. At-rest encryption is part of pre-exposure
roadmap item 7. The *send-side* boundary (PII never reaches an LLM un-redacted) is already
enforced — see the redaction seam in
[code_organization.md](code_organization.md#the-load-bearing-seams-do-not-break-these).

---

## Quick reference

| Concern | Where | Key fact |
|---|---|---|
| Connection / PRAGMAs | `database.py::get_connection` | WAL + 15s busy_timeout |
| Concurrent run execution | `workflows.py::_executor` | in-process thread pool, single process |
| Concurrent scoring writers | `score_jobs.py` | 5-thread fan-out; the one multi-writer site |
| Single-flight + cancel | `run_control.py` | in-memory, lock-protected, single-process |
| Lost paid output | the runners + nodes | now surfaced to `errors[]`/`persisted:false` (fixes 1-2) |
| Cost backstop | `limits.py` `MAX_LLM_CALLS_PER_RUN` | soft governor, not transactional |
| Two state stores | `workflow_runs` vs `checkpoints` | non-atomic; read `workflow_runs` for UI |
