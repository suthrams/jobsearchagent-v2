# ADR-082: Idempotent Workflow Kickoff + In-Flight Execution Guard

## Status

Accepted (2026-06-04). Implemented.

Hardens the run-kickoff surface introduced in ADR-004 and the phase-2 / retry
re-entry endpoints (ADR-060, plus the `POST /workflows/{id}/retry` recovery path).
Companion to ADR-083 (run cancellation); both are run-lifecycle controls over
HTTP.

## Context

A workflow run is not a cheap CRUD write. Each run pays for real LLM calls
(discovery research + scoring + deep review, bounded by `MAX_LLM_CALLS_PER_RUN`).
A duplicate run is therefore a duplicate **bill**, not a duplicate row. This is the
axis on which an agentic API differs from a classic enterprise API: a retried
mutating call wastes dollars, not just a database insert.

Three concrete double-spend holes existed before this ADR:

1. **`POST /workflows`** mints a fresh `workflow_id` per call and submits to the
   thread pool with no dedup. A client that sends the same kickoff twice (an
   automatic network retry after a slow-but-successful POST, a proxy replay) pays
   twice.
2. **`POST /workflows/{id}/retry`** re-invokes a thread from its checkpoint. It
   guards only on `snapshot.next` being non-empty; it does not check whether the
   thread is *already executing*. Two near-simultaneous retries can drive two
   `graph.invoke()` calls against the same `thread_id` / checkpoint, which both
   corrupts state and double-bills.
3. **`POST /workflows/{id}/scoring`** (ADR-060 phase 2) guards on
   `status == "awaiting_scoring_selection"`. That is a real backstop, but it is a
   read-then-act check with a race window: two requests can both read
   `awaiting_scoring_selection` before either flips the status to `running`.

The industry-standard answer to (1) is the Stripe-style `Idempotency-Key` header,
now also an HTTP-standard draft. The answer to (2)/(3) is a single-flight guard on
the natural dedup key, which for a re-entry endpoint is the `workflow_id` itself.

## Decision

Add two complementary mechanisms. Neither changes existing behavior when its
trigger is absent (backward compatible).

### A. `Idempotency-Key` header on `POST /workflows`

The kickoff endpoint accepts an optional `Idempotency-Key` request header. When
present, the server claims the key atomically and replays instead of starting a
second run:

1. Mint the `workflow_id` and build the full kickoff response (everything in the
   response is known before the run is submitted).
2. Compute a `request_fingerprint` = SHA-256 over the canonical JSON of the
   request as submitted (`user_id`, `resume_id`, `search_criteria`,
   `workflow_type`, the caller's `effective_config` *before* the per-run agent
   snapshot is injected, and `custom_urls`).
3. **Insert-first claim** into a new `idempotency_keys` table (the key is the
   PRIMARY KEY, so the insert is the atomic lock):
   - Insert succeeds -> this is the first call. Submit the run, return `202` with
     the stored response.
   - Insert hits the PK constraint -> the key already exists. Read the stored row:
     - **Same fingerprint** -> replay: return the stored `202` response, do **not**
       start a second run.
     - **Different fingerprint** -> `409 idempotency_key_reused` (the key was
       already used for a materially different request).

Absent the header, `POST /workflows` behaves exactly as before.

The Streamlit UI (the only API consumer, ADR-075) generates a fresh key per
kickoff call so an automatic retry of the same submission is deduped end to end.
A user deliberately starting a second run is a new submission with a new key and
is correctly allowed (a legitimate second run, not a duplicate).

### B. In-flight execution guard on the re-entry endpoints

A process-level single-flight registry (`app/workflows/run_control.py`,
lock-protected set of currently-executing `workflow_id`s). `POST .../retry` and
`POST .../scoring` `try_acquire_running(workflow_id)` before submitting to the
pool; a `workflow_id` already executing is rejected with
`409 workflow_already_running`. The run wrappers release the id in a `finally`.
This closes the read-then-act races in (2) and (3) deterministically, independent
of checkpoint-status timing.

`POST /workflows` also acquires the guard for its fresh id (always succeeds; kept
for symmetry and so the replay path never double-submits).

### Schema

New table, additive, created by `CREATE TABLE IF NOT EXISTS` in `_SCHEMA_SQL`
(no migration needed; brand-new table):

```sql
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key      TEXT PRIMARY KEY,
    user_id              TEXT,
    endpoint             TEXT NOT NULL,
    request_fingerprint  TEXT,
    workflow_id          TEXT,
    response_json        TEXT,
    created_at           TEXT NOT NULL
);
```

`IdempotencyRepository.claim(...)` implements the insert-first claim and returns
`("claimed" | "replay" | "conflict", row)`.

## Options considered

- **Idempotency-Key header (chosen for kickoff).** Standard, explicit, lets the
  client own the operation identity. The right tool when the dedup key is the
  request, not a resource that already exists.
- **In-flight single-flight guard (chosen for re-entry).** For `retry`/`scoring`
  the dedup key is the existing `workflow_id`; a header would be redundant. A
  process-local guard is the minimal correct fix for the read-then-act race.
- **Natural-key dedup on `POST /workflows` (rejected).** Fingerprinting the body
  and refusing a "same body within N seconds" submit would block legitimate
  intentional re-runs and bakes in a guessed window. The explicit header is
  cleaner and does not surprise the user.
- **Distributed/shared idempotency store (rejected for now).** The API server and
  the executor run in **one process** (the thread pool lives in the router
  module), so an in-memory single-flight guard is authoritative. A multi-worker
  uvicorn deployment would need shared state; out of scope for this single-process
  local tool, flagged below.

## Consequences

### Positive

- A retried kickoff costs one run, not two. The agentic-specific failure mode
  (a retry = a duplicate bill) is closed with the standard mechanism.
- The `retry` double-invoke (state corruption + double spend) and the `scoring`
  read-then-act race are closed deterministically.
- Both mechanisms are opt-in / trigger-gated, so Primary and existing callers are
  unchanged.

### Tradeoffs / limits (honest)

- The in-flight guard is **process-local**. Correct for the current single-process
  deployment; a multi-worker rollout would need a shared lock (Redis/DB advisory
  lock). Documented, not built.
- The `idempotency_keys` table is not PII and is small (ids + a hash + a stored
  response). It is not yet wired into the ADR-070 retention purge; a TTL sweep is a
  minor follow-up.
- The header dedups retries of the *same call*. It does not (and should not) stop
  a user from intentionally starting a second run from the UI, which is a new
  submission with a new key.

### Neutral

- Docs (architecture-docs sweep mandate): this ADR + index, `api_reference.md`
  (Idempotency-Key on `POST /workflows`, the `409 idempotency_key_reused` and
  `409 workflow_already_running` error codes), `api_surface_overview.md`,
  `data_model.md` (the `idempotency_keys` table), `workflow_model.md` (kickoff
  dedup note), CLAUDE.md (orchestration rules), CHANGELOG. Tests: `run_control`
  single-flight unit tests; idempotent-kickoff replay / conflict / no-key paths;
  re-entry `workflow_already_running` guard.

## References

- ADR-004 — the workflow kickoff + async-poll surface this hardens.
- ADR-060 — the phase-2 scoring re-entry endpoint guarded here.
- ADR-083 — run cancellation (the companion run-lifecycle control; shares
  `app/workflows/run_control.py`).
- Stripe, *Idempotent requests* — the `Idempotency-Key` pattern this follows
  (external, for contrast only).
