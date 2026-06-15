# ADR-106: Fail-Loud Startup Guard for Unsafe Deployment (Multi-Worker / Non-Loopback)

## Status

- **Accepted** (2026-06-14). Implements roadmap **item 4** from the
  [2026-06-13 architecture review](../architecture_review_2026-06-13.md) (§3).
- Hardens the single-process + cooperative-trust assumptions documented across
  ADR-062 (no-auth `?user_id=`), ADR-082 (in-process idempotency), ADR-083
  (in-process cancellation), ADR-096 (in-process run recovery).

## Context

- The whole runtime is **single-process** and **cooperative-trust**: the workflow
  executor, the idempotency registry, the run-control registries, and run recovery
  are all in-memory; identity is an unauthenticated `?user_id=` query param with no
  ownership checks.
- These are **correct scope cuts for the intended single-user, loopback deployment**
  — but **nothing trips when a deployment violates them.** The review called this "the
  silent deployment cliff":
  - `--workers 2` / `WEB_CONCURRENCY>1` -> each worker has its own registries, so
    idempotency, the single-flight guard, cancellation, and recovery all bypass across
    workers -> **double-runs and double-spend** (cost is the stated #1 concern).
  - A non-loopback bind (`--host 0.0.0.0`) with no auth -> **cross-tenant
    read/write/delete** of resumes, configs, and favorites.
- Today both happen with **no error and no log** — the operator finds out via a billing
  surprise or a data leak.

## Decision

Add a **best-effort startup tripwire** that detects these two misconfigurations from
process-visible state and, by default, **refuses to start**.

- New pure module `app/api/deployment_guard.py`:
  - `detect_unsafe_deployment(argv, environ) -> list[str]` — returns human-readable
    violation strings (empty = safe). Pure (args injected), so it is unit-testable
    without a live server.
  - `enforce_deployment_safety(argv=None, environ=None)` — computes violations; on
    none, returns; otherwise either raises `UnsafeDeploymentError` (default) or logs a
    prominent warning and continues (when the escape hatch is set).
- **Detection signals** (process-visible, no sibling-process counting):
  - *Multi-worker:* `WEB_CONCURRENCY` env int `> 1`, OR `--workers N` / `--workers=N`
    in `sys.argv` with `N > 1`.
  - *Non-loopback bind:* `--host H` / `--host=H` in `sys.argv` where `H` is not in the
    loopback allowlist `{127.0.0.1, ::1, localhost}`. No `--host` = uvicorn's loopback
    default = safe.
- **Escape hatch:** `ALLOW_UNSAFE_DEPLOYMENT` truthy (`1/true/yes/on`) downgrades the
  hard fail to a single prominent `WARNING` and starts anyway. Preserves operator
  agency for an advanced/known-safe topology without making "unsafe" the silent default.
- **Wiring:** called first in the FastAPI `lifespan`, inside the existing
  `get_graph not in app.dependency_overrides` block (real-startup only), **before**
  `build_and_cache_graph()` — so it fails before any expensive wiring and before
  recovery re-submits work under an unsafe posture. Tests inject deps via
  `dependency_overrides`, so the suite never trips the guard.

## Decision review (not a rubber-stamp)

- **Recommendation / confidence:** ship it; **high** confidence on the value and on
  detecting the common cases, **medium** on total coverage (see limitations).
- **The ONE load-bearing decision: refuse-to-start (default) vs warn-and-continue.**
  Chose **refuse**. The defect is *silence* — a log warning is also easy to miss, and a
  second worker double-spends real money on the first run. A hard fail is the only thing
  that actually prevents the damage; the escape hatch keeps the warn-and-continue option
  one env var away.
- **Pros:** ~1 small module + one lifespan call converts a silent cost/exposure cliff
  into a loud, self-explaining failure; pure detector is trivially testable; zero effect
  on the normal single-process loopback run and on tests.
- **Cons / risks (estimated, not measured):**
  - *False positive:* a container that binds `0.0.0.0` but is only reachable on loopback
    via port mapping is flagged though effectively safe -> the escape hatch is the
    intended remedy (documented).
  - *False negative (coverage gap):* heuristic parsing of `sys.argv`/env does **not**
    catch programmatic `uvicorn.run(host=...)`, a gunicorn config-file `workers`, or
    other launch methods. It is a tripwire for the **common** misconfigurations, not a
    sandbox.
  - *Correctly NOT flagged:* a reverse proxy (TLS) in front of a loopback-bound app —
    that app binds loopback, which is the safe pattern.
- **Reversibility / cost:** trivially reversible — delete the call or set
  `ALLOW_UNSAFE_DEPLOYMENT=1`. Low cost, no schema/contract change.
- **Where I took the easy path:** detection is heuristic (argv + env), not a
  process-table scan; accepted because portable sibling-worker counting is unreliable
  and the intent signals (`WEB_CONCURRENCY`/`--workers`/`--host`) cover the realistic
  failure modes.
- **Reasons a reviewer might say NO:** "premature — the app isn't deployed." Counter:
  it is the cheapest, highest-value remaining guard, and the failure it prevents
  (double-spend / cross-tenant leak) is exactly the one the review rated Critical-if-
  deployed. "Drop the fragile host check." Counter: the non-loopback bind is the
  *exposure* vector while item 7 (auth) is open, so it is worth catching even
  best-effort.

## How it integrates

- Runs in `lifespan` before `build_and_cache_graph()`; raising aborts startup loudly.
- Honors the existing test seam: skipped when a test injects a graph via
  `dependency_overrides`, so CI (TestClient, no `--host`/`--workers`,
  `WEB_CONCURRENCY` unset) is unaffected.
- Mode-agnostic (live and mock) — the unsafe posture is dangerous regardless of the
  Phase-7 agent gate.

## Out of scope

- **Does not** make the app multi-worker-safe or exposable — it makes the *attempt*
  fail loud. The real fixes are a shared run/idempotency store (multi-worker) and auth
  + ownership + at-rest encryption (exposure, roadmap item 7).
- No process-table scanning; no detection of every possible launch method.
- No new endpoint, schema, or config knob (the only env vars are the existing
  `WEB_CONCURRENCY` and the new opt-out `ALLOW_UNSAFE_DEPLOYMENT`).

## PSSR

- **Performance/Scalability:** one-time string/int parse at startup; zero runtime cost.
- **Security:** turns a silent no-auth exposure into a refused boot; the error message
  names the violation class only (no secret/host-value leakage beyond what the operator
  themselves passed on the command line).
- **Reliability:** pure, deterministic detector; default-safe; escape hatch prevents it
  from becoming an operational footgun for a legitimately advanced topology.

## Tests

- Forcing-function unit tests on `detect_unsafe_deployment`: clean argv/env -> no
  violation; `--host 0.0.0.0` and `--host=::` -> violation; loopback hosts -> none;
  `WEB_CONCURRENCY=4` and `--workers 2`/`--workers=2` -> violation; `--workers 1` ->
  none; combined signals accumulate.
- `enforce_deployment_safety` raises `UnsafeDeploymentError` on violations and is a
  no-op with the escape hatch set (downgrade-to-warning path).

## References

- `docs/architecture/architecture_review_2026-06-13.md` (§3, roadmap item 4),
  `app/api/deployment_guard.py`, `app/api/main.py` (lifespan),
  `app/workflows/run_control.py`, ADR-062/082/083/096.
