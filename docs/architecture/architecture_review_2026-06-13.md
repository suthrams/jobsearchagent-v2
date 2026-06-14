# Architecture & Design Review — 2026-06-13

**Scope:** Whole-application structural and behavioral weaknesses.
**Method:** Read-only static review across three axes (concurrency/persistence,
error-handling/data-integrity, security/isolation), cross-checked against the ADR trail
and `CLAUDE.md` invariants. Findings cite `file:line` evidence.
**Calibration:** These are **static-analysis findings (estimated severity)** — no
concurrency stress test was run and no data loss was reproduced. "Can happen" means the
code path allows it, not that it was measured. Severity is rated for **two** contexts:
*Now* (the intended single-user, locally-run, cooperative-trust deployment) and
*If-deployed* (multi-worker, exposed, or multi-tenant).

---

## Executive summary

The system is **well-factored and unusually self-aware**. The seams that are expensive
to retrofit are done right: UI-reads-through-API (ADR-075), provider abstraction,
orchestrator-only-mutates-state, prompt-injection guardrails enforced at the
infrastructure layer (not by convention), and solid SSRF defenses. The ADR trail is
honest about its own scope cuts.

The weaknesses are **not sloppiness — they are deliberate scope cuts that are
individually documented but collectively form a hard ceiling, and nothing fails loudly
when a deployment crosses that ceiling.**

**The single load-bearing risk:** the codebase is saturated with *single-process*,
*cooperative-trust*, and *best-effort-persistence* assumptions, and there is **no guard
that trips when those assumptions are violated.** Two consequences dominate:

1. **Silent data loss on the unhappy path (real today).** Every paid agent output
   (score, review, advice, tailoring, interview prep) is persisted inside a
   `try/except: log + continue`, and the caller still reports success. A failed write
   loses the result *and* re-spends LLM cost on the next run — with no error surfaced.
2. **A silent deployment cliff.** Run with `--workers 2`, expose the port, or change the
   single-user assumption, and authz, idempotency, cancellation, and run-recovery break
   — with no startup check to catch it.

**Net verdict:** a strong single-user system with a clearly documented ceiling. It is
**not ready** to be deployed multi-worker, exposed, or multi-tenant as-is — and the
biggest in-context bug is invisible failure, not a crash. The highest-value fixes are
small and well-contained (see the roadmap); the deepest gap (no quality feedback loop)
is inherent to the product stance and needs offline evaluation, not telemetry.

| Theme | Severity *Now* | Severity *If-deployed* | Cost to fix |
|---|---|---|---|
| Swallowed persist failures → silent loss + re-spend | **High** | High | Low |
| Non-idempotent on-demand score → dup rows + double-spend | **Medium-High** | High | Low |
| Soft (non-atomic) cost cap under concurrency | Medium | Medium | Medium |
| SQLite write config (no WAL/busy_timeout) under 5 writers | Low-Medium | High | Low |
| No schema-migration versioning | Low | Medium | Low |
| Dual state stores can diverge on mid-run crash | Medium | Medium | Medium |
| No auth / ownership checks (`?user_id=`) | n/a (by design) | **Critical** | High |
| PII unencrypted at rest | Low (by design) | **High** | Medium |
| Single-process registries / in-process executor | n/a (by design) | **High** | High |
| No closed loop on agent output quality | Medium | Medium | High |

---

## 1. Overall assessment

- **Structure:** clean separation of orchestrator / agents / services / repositories /
  providers; agents depend only on `LLMClient`; UI never opens the DB. These hold.
- **Resilience philosophy:** "never lose the run" (keep-all on filter failure) is applied
  consistently at discovery. It is correct *there* but has been over-generalized into the
  *persistence* layer, where swallowing a failure means losing paid output, not just a
  filter pass (see §2.1).
- **Self-awareness:** `CLAUDE.md` and the ADRs flag most single-process / multi-worker /
  at-rest gaps explicitly. The problem is not that they are unknown; it is that nothing
  *enforces* the assumption that keeps them safe (see §3).

---

## 2. Critical findings that bite *in the intended single-user context*

### 2.1 Persist-failures are swallowed with a success response → silent loss + re-spend
**Severity Now: High.** Most important finding: needs neither multi-user nor
multi-process to fire. Every domain write is `try/except: log + continue`, and the caller
returns success regardless.

- `app/services/scoring_runner.py:104-107` — `score_repo.create` fails → score returned
  in-memory, **never persisted**, reported `status:"scored"`. The next run re-discovers
  and **re-spends** on the same job.
- Same pattern: `app/services/deep_review_runner.py:121-128` and `:164-170` (review
  rounds + final review), `app/workflows/nodes/career_advice.py:88-91`,
  `app/api/routers/tailoring.py:291-307` (falls back to an in-memory dict, masking the
  failure) and `:602-606` (interview prep), `app/workflows/nodes/discover_jobs.py:146-152`.

**Compounding chain:** `app/repositories/database.py:352-363` opens a fresh connection
per call with **no WAL and no explicit busy configuration**, and scoring fans out across
**5 concurrent writer threads** (`app/workflows/nodes/score_jobs.py:36,111`). Under write
contention a lock/`SQLITE_BUSY` can raise inside `create` → swallowed → score lost, user
told it succeeded, money re-spent. Low probability in practice (writes are fast, LLM
latency dominates, sqlite3's default 5s busy-wait absorbs most collisions), but invisible
when it fires.

**Recommendation:** failures to persist *paid* agent output must be loud — surface to the
run's `errors[]` / status — not swallowed like the never-lose-the-run discovery filters.

### 2.2 On-demand score is not idempotent under concurrency → duplicate rows + double-spend
**Severity Now: Medium-High.** `app/api/routers/tailoring.py:475-487` does a
read-then-act idempotency check with a gap, and `job_scores` has **no UNIQUE constraint
on `(workflow_run_id, job_id)`** (`app/repositories/database.py:80`). Two concurrent
requests (double-click, retry) both pass the check and both insert. The process-local
`run_control` registry guards `/scoring` and `/retry` but **not** this single-job
endpoint.

**Recommendation (cheap, high value given cost is the stated #1 concern):** add
`UNIQUE(workflow_run_id, job_id)` to `job_scores` and use `INSERT OR IGNORE`.

### 2.3 The cost cap is best-effort, not a hard ceiling
**Severity Now: Medium.** `MAX_LLM_CALLS_PER_RUN` is read **once** before the 5-thread
fan-out (`app/workflows/nodes/score_jobs.py:64-69`); the counter is updated in-memory
**after** all threads finish (`:142`). Concurrent threads plus a research retry can
overshoot the pre-flight estimate. The conservative `//2` and per-run scope bound the
blast radius. Worth documenting that the cap is a soft governor, not a guarantee.

### 2.4 Schema evolution has no versioning
**Severity Now: Low.** `app/repositories/database.py:370-426` is an accumulating list of
`try/except: pass` `ALTER TABLE`s with no `schema_version` table. A failure for any
reason *other than* "column exists" is swallowed, leaving a silently-inconsistent schema
with no audit trail. Fine at this size; a liability as it grows.

### 2.5 Two state stores can diverge on a mid-run crash
**Severity Now: Medium.** LangGraph's `checkpoints` table and `workflow_runs` are written
separately and **not atomically** (`app/workflows/nodes/register_run.py:39` vs the graph
checkpointer). A crash between them can leave the UI reading `status:"running"` forever
while the checkpoint says done. ADR-096 recovery + `reconcile_orphaned_runs` mitigate the
*restart* case; an un-restarted process leaves a stuck row.

---

## 3. Weaknesses deferred by design — the silent deployment cliff

These are **correctly out of scope** for the intended deployment and therefore **not
bugs today.** The structural risk is that **nothing enforces the assumption that keeps
them safe.**

- **No auth; `?user_id=` is the only boundary.** `app/api/identity.py:39-58` resolves
  identity from a query param with no authentication and **no ownership checks**:
  `DELETE /users/{user_id}/resume/{resume_id}`, the clinic
  (`app/api/routers/resume_clinic.py:231-303`), and favorites
  (`app/api/routers/favorites.py:27-66`) all trust the param. Safe **only** because
  single-user and not exposed. *If-deployed:* full cross-tenant read/write/delete.
- **PII unencrypted at rest.** `resumes.raw_text` + `parsed_profile_json` (names, emails,
  phones) and `workflow_runs.state_json` are plaintext
  (`app/repositories/database.py:68-78`). ADR-070 flags Phase-2 encryption as pending.
- **Single-process everything.** `run_control` registries, the module-level `_executor`,
  idempotency, and recovery are in-memory (`app/workflows/run_control.py:1-9`,
  `app/api/routers/workflows.py:43-50`). `--workers 2` → double-runs, lost cancellation,
  idempotency bypass.

**Architectural recommendation:** add a **startup assertion that fails loud** when the
environment violates the assumption — detect `WEB_CONCURRENCY`/`--workers > 1` or a
non-loopback bind host and refuse to start (or log a prominent warning). Today the cliff
is silent.

---

## 4. Behavioral weaknesses

- **No closed loop on output quality (deepest gap).** By design the app *cannot measure
  its own success* (no outcome tracking), and behavioral model drift is acknowledged as
  the hardest thing to pin down. A prompt change or model swap can degrade real-world fit
  quality **with no signal** — the model-pin tests catch *schema* drift, not *judgment*
  drift. Inherent to the "filter, not tracker" stance; the mitigation is offline eval
  sets, not telemetry.
- **Dead memory scaffolding.** `memory_items` + `MemoryRepository` exist but nothing
  reads/writes them; `state_and_memory_model.md` describes behavior that does not run.
  Design-doc-vs-reality drift is a trap for the next reader.
- **`WorkflowState` TypedDict landmine.** LangGraph silently drops state keys not declared
  in the TypedDict (root cause of earlier custom-URL bugs). A fragile contract enforced by
  nothing.
- **"Never lose the run" masks bugs.** Keep-all-on-failure
  (`app/workflows/nodes/relevance_filter.py:134-158`) cannot distinguish "agent errored"
  from "agent returned empty/garbage" — both look like success-with-no-filtering.

---

## 5. What is genuinely strong (confirmed)

The expensive-to-retrofit parts are correct:

- **SSRF defenses.** Redirect re-validation + IP-class rejection
  (`app/services/url_safety.py:64-133`); the Workday host guard is the single parse seam
  used by both the scraper and the verify endpoint (`app/services/workday_scraper.py:75-106`).
- **Prompt-injection guardrails by architecture, not convention.**
  `app/providers/prompt_loader.py:133-137` prepends `guardrails.txt` to *every* agent;
  all agents funnel through this seam.
- **No secret leakage.** Readiness/health report presence/mode only; `security_events` /
  `api_requests` / `llm_calls` store PII-safe metadata only.
- **Clean read/write seams.** UI-reads-through-API (enforced by an invariant test),
  provider abstraction, orchestrator-only-mutates-state, model-pin invariant tests.

---

## 6. Prioritized remediation roadmap

| # | Action | Addresses | Cost | Value |
|---|---|---|---|---|
| 1 | Stop swallowing **paid-output** persist failures — surface to `errors[]`/status | §2.1 | Low | High |
| 2 | `UNIQUE(workflow_run_id, job_id)` on `job_scores` + `INSERT OR IGNORE` | §2.2 | Low | High |
| 3 | `PRAGMA journal_mode=WAL` + explicit `busy_timeout` in `get_connection` | §2.1 chain | Low | Medium |
| 4 | Startup guard: fail loud on multi-worker / non-loopback bind | §3 | Low | High |
| 5 | `schema_version` table before the migration list grows further | §2.4 | Low | Medium |
| 6 | Offline agent-output eval set (the only quality signal available) | §4 | High | High |
| 7 | (Pre-exposure, separate track) auth + ownership checks; at-rest encryption | §3 | High | Critical-if-deployed |

Items 1-5 are small, well-contained, and improve the system *in its current context*.
Item 6 is the strategic investment. Item 7 is a gate that must precede any exposed or
multi-tenant deployment.

**Implementation status (2026-06-13):** roadmap items **1-3 are implemented** the same
day. Item 1 — paid-output persist failures now surface to `errors[]` / a `persisted:false`
API flag (scoring, career advice, deep review, interview prep). Item 2 —
`UNIQUE(workflow_run_id, job_id)` on `job_scores` + `INSERT OR IGNORE` (with a dedupe-safe
migration). Item 3 — `PRAGMA journal_mode=WAL` + a 15s `busy_timeout` in `get_connection`.
Note: item 6's tailoring-create finding (review draft §2.1 list) was **validated and
dropped** — `tailoring_repo.create` is not wrapped in a swallowing try/except, so a
persist failure raises (500) rather than being silently lost. Items 4-7 remain open.

---

## Appendix — evidence index

- Connections / no WAL: `app/repositories/database.py:352-363`
- Concurrent scoring writers: `app/workflows/nodes/score_jobs.py:36,111`; write at
  `app/repositories/score_repository.py:21`
- Swallowed persists: `app/services/scoring_runner.py:104-107`;
  `app/services/deep_review_runner.py:121-128,164-170`;
  `app/workflows/nodes/career_advice.py:88-91`;
  `app/api/routers/tailoring.py:291-307,602-606`;
  `app/workflows/nodes/discover_jobs.py:146-152`
- Idempotency gap: `app/api/routers/tailoring.py:475-487`; `job_scores` schema
  `app/repositories/database.py:80`
- Cost cap: `app/workflows/nodes/score_jobs.py:64-69,142`
- Migrations: `app/repositories/database.py:370-426`
- Dual state stores: `app/workflows/nodes/register_run.py:39`; graph checkpointer wiring
- Single-process: `app/workflows/run_control.py:1-9`; `app/api/routers/workflows.py:43-50`
- Identity / authz: `app/api/identity.py:39-58`; `app/api/routers/resume_clinic.py:231-303`;
  `app/api/routers/favorites.py:27-66`
- PII at rest: `app/repositories/database.py:68-78`
- SSRF (good): `app/services/url_safety.py:64-133`; `app/services/workday_scraper.py:75-106`
- Guardrails (good): `app/providers/prompt_loader.py:133-137`
</content>
