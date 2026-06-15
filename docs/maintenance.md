# Maintainer Handbook

> **Audience:** an engineer inheriting this codebase cold. You are competent, but you
> have zero context on *this* system. This handbook is the on-ramp: it gives you the
> mental model, the load-bearing assumptions you must not violate, and a guided reading
> path that takes you from "I can run it" to "I can change it safely."
>
> **This is the operator/maintainer view.** For the *end-user* walkthrough (configure a
> profile, run a search, read a report) see [user_guide.md](user_guide.md). For *why*
> each design decision was made, follow the ADR links — the durable reasoning lives in
> [architecture/principles.md](architecture/principles.md) and the ADR trail, not here.

---

## How this handbook is organized

The maintenance docs follow [Diataxis](https://diataxis.fr/): each doc serves **one**
need and does not mix them. Use the table to jump to what you need *right now*.

| Doc | Type | Read it when you need to... |
|---|---|---|
| **This page** | Explanation / map | Understand the system, its assumptions, and where everything is |
| [maintenance/running_and_operating.md](maintenance/running_and_operating.md) | How-to + reference | Start the backend + UI, set env vars, switch live/mock mode, reload config |
| [maintenance/code_organization.md](maintenance/code_organization.md) | Explanation + reference | Understand the layering, the load-bearing seams, and find the module that owns a concern |
| [maintenance/persistence_and_concurrency.md](maintenance/persistence_and_concurrency.md) | Explanation + reference | Reason about SQLite/WAL, the single-process execution model, and where writes can be lost |
| [maintenance/schema_and_migrations.md](maintenance/schema_and_migrations.md) | How-to + reference | Add or change a database column safely; understand the migration discipline |
| [maintenance/backup_restore_and_troubleshooting.md](maintenance/backup_restore_and_troubleshooting.md) | How-to | Back up / restore the data, or fix a symptom (stale code, stuck run, port in use) |

**Complementary docs you will lean on (not duplicated here):**

- [architecture/architecture_overview.md](architecture/architecture_overview.md) — the system layers and design principles, in depth.
- [architecture/architecture_review_2026-06-13.md](architecture/architecture_review_2026-06-13.md) — the point-in-time review that produced this handbook's "assumptions" and "roadmap" sections.
- [architecture/data_model.md](architecture/data_model.md) — every table, column, and index.
- [architecture/api_reference.md](architecture/api_reference.md) — the REST contract.
- [cost_troubleshooting.md](cost_troubleshooting.md) + [model_recommendations.md](model_recommendations.md) — cost diagnosis and per-agent model picks.
- [settings_reference.md](settings_reference.md) — every config knob and what it changes.
- [adr/ADR-000-index.md](architecture/adr/ADR-000-index.md) — the canonical decision log.

---

## What this system is (in one screen)

A multi-agent career-intelligence app. A LangGraph **orchestrator** runs a fixed job-search
workflow (discover -> optional relevance filter -> research -> score -> deep-review the
qualifiers -> advise -> report). Specialized **agents** (Claude/OpenAI via a provider
abstraction) produce structured outputs; deterministic **services** do the non-LLM work
(scraping, parsing, filtering, rendering); **repositories** persist to SQLite. A FastAPI
**backend** exposes the workflow + on-demand operations (tailoring, deep review, interview
prep, the Resume Clinic) over REST. A Streamlit **UI** is a thin control surface that
reads and writes *only* through the API.

The deeper structure — the layers, the seams that are expensive to retrofit, and a tour
of the key modules — is in
[maintenance/code_organization.md](maintenance/code_organization.md). Read that second
(right after you can run the app).

---

## The three load-bearing assumptions (read before you change anything)

The 2026-06-13 architecture review's headline finding: the codebase is **saturated with
three assumptions**, they are individually documented across the ADRs, and **nothing
fails loudly when a deployment violates them.** If you internalize one thing from this
handbook, make it these three.

### 1. Single-process

The FastAPI app, the workflow thread pool (`_executor`), the idempotency registry, the
run-control registries, and run-recovery are all **in-memory in one process**
(`app/workflows/run_control.py:1-9`, `app/api/routers/workflows.py`). They are
*authoritative only because there is exactly one process.*

> **Do not run `--workers 2` / set `WEB_CONCURRENCY>1`.** It breaks idempotency,
> cancellation, the single-flight guard, and run recovery — a second worker double-runs
> jobs and double-spends. As of ADR-106 the startup guard (`app/api/deployment_guard.py`)
> **refuses to boot** on this and on a non-loopback bind, so the failure is now loud rather
> than silent (override: `ALLOW_UNSAFE_DEPLOYMENT=1`). See
> [maintenance/persistence_and_concurrency.md](maintenance/persistence_and_concurrency.md).

### 2. Cooperative-trust (no auth)

Identity is a `?user_id=` query param resolved in one place
(`app/api/identity.py::get_current_user_id`, ADR-062), validated against the `users`
table but **not authenticated**, and there are **no ownership checks**. This is safe
*only* because the app is single-user and bound to loopback. Exposing the port turns
every profile-scoped endpoint into a cross-tenant read/write/delete.

> **Do not bind to a non-loopback host or expose the port** without first doing the
> pre-exposure work (auth + ownership + at-rest encryption — roadmap item 7 below). The
> ADR-106 startup guard refuses to boot on a `--host` non-loopback bind as a tripwire, but
> it is *not* a substitute for that work — it only makes the misconfiguration loud.

### 3. Best-effort persistence

Discovery filters follow a deliberate "never lose the run" rule (a filter failure keeps
all jobs rather than dropping the run). That rule was historically *over-generalized* into
the persistence layer, where swallowing a failure means losing **paid** agent output. The
2026-06-13 review's roadmap items 1-3 hardened the worst of this (paid-output writes now
surface to `errors[]` / a `persisted:false` flag; `job_scores` got a uniqueness
constraint; WAL + a busy timeout were added). The *principle* still holds:

> Persisting **paid** agent output must be **loud**, not swallowed. When you add a new
> agent or write path, surface a persist failure to the run's `errors[]` / status — never
> `try/except: log + continue` the way discovery filters do. See
> [maintenance/persistence_and_concurrency.md](maintenance/persistence_and_concurrency.md).

---

## Onboarding reading path (Day 1 -> Week 2)

A phased path, in the spirit of a 30-60-90 ramp. Do it in order; each step assumes the
previous one.

**Day 1 — get it running and oriented**

1. Read this page top to bottom (you're here).
2. Follow [maintenance/running_and_operating.md](maintenance/running_and_operating.md):
   set env vars, start the backend + UI, confirm `GET /health` and `GET /readyz`.
3. Skim [user_guide.md](user_guide.md) and run one search end-to-end in **mock mode**
   (no `ANTHROPIC_API_KEY`) so you see the workflow shape with zero spend.

**Week 1 — build the mental model**

4. Read [maintenance/code_organization.md](maintenance/code_organization.md) — the layers,
   the seams, and the key-modules tour. This is the map you'll use for every change.
5. Read [maintenance/persistence_and_concurrency.md](maintenance/persistence_and_concurrency.md)
   — internalize the single-process + best-effort-persistence assumptions in code.
6. Skim [architecture/workflow_model.md](architecture/workflow_model.md) and
   [architecture/agent_model.md](architecture/agent_model.md) for the per-node and
   per-agent contracts.

**Week 2 — make a safe change**

7. Read [maintenance/schema_and_migrations.md](maintenance/schema_and_migrations.md) before
   touching the schema, and [CLAUDE.md](../CLAUDE.md) for the workflow rules (ADR-first,
   docs sweep, render-verify, run the tests).
8. Make a small change behind a test. Run the suite ([testing.md](testing.md) is the single
   source of truth for how). Do the architecture-docs sweep. Commit.

---

## The open roadmap (what is deliberately not done)

From the 2026-06-13 review. Items 1-3 shipped that day and **item 4 shipped 2026-06-14
(ADR-106)**; **5-7 are open.** Treat this as the backlog of known ceilings, each one a
documented scope cut rather than a bug.

| # | Item | Why it matters | Cost |
|---|---|---|---|
| 4 | **Startup guard: fail loud on multi-worker / non-loopback bind** — *done (ADR-106)* | Makes the single-process + cooperative-trust cliff *non-silent* (`app/api/deployment_guard.py`; override `ALLOW_UNSAFE_DEPLOYMENT`). Best-effort tripwire, not a sandbox | Low |
| 5 | **`schema_version` table** before the migration list grows further | Today migrations are an accumulating list of idempotent `ALTER`s with no version audit (see schema doc) | Low |
| 6 | **Offline agent-output eval set** | The app cannot measure its own success (no outcome tracking, by design). Model/prompt *judgment* drift has **no signal** today — only schema drift is caught (model-pin tests). This is the deepest gap | High |
| 7 | **(Pre-exposure track) auth + ownership checks; PII at-rest encryption** | The gate that must precede ANY exposed or multi-tenant deployment. PII (`resumes.raw_text`, `parsed_profile_json`, `workflow_runs.state_json`) is plaintext at rest (ADR-070 Phase 2 pending) | High |

Two more known-deferred realities a new maintainer trips over:

- **Long-term memory is designed, not wired.** `memory_items` + `MemoryRepository` exist;
  nothing reads/writes them. There is no `MemoryService` / `app/memory/`. Treat
  [architecture/state_and_memory_model.md](architecture/state_and_memory_model.md) as a
  design contract, not current behavior.
- **`WorkflowState` is a `TypedDict` landmine.** LangGraph silently drops state keys not
  declared in the TypedDict (root cause of earlier custom-URL bugs). If a value vanishes
  between nodes, check the schema declaration first.

---

## Glossary (terms you'll meet immediately)

| Term | Meaning |
|---|---|
| **Orchestrator** | The LangGraph `StateGraph` in `app/workflows/` that runs the workflow. Only it mutates `WorkflowState`. |
| **Agent** | An LLM-using component (`app/agents/`, all inherit `BaseAgent`). Returns a validated Pydantic schema; never touches the DB/filesystem/network directly. |
| **Service** | Deterministic, non-LLM work (`app/services/`): scraping, parsing, filtering, rendering. |
| **Repository** | SQLite data access (`app/repositories/`). Each owns its `get_connection()` calls. |
| **Provider** | The `LLMClient` abstraction (`app/providers/`). Agents depend on it, never on a concrete Claude/OpenAI class. Wired via `ModelRegistry`. |
| **Out-of-graph operation** | An on-demand op (tailoring, deep review, interview prep, Resume Clinic) that runs an agent directly outside the LangGraph state machine, reading state from the checkpointer and persisting via repos. No `interrupt()`. |
| **Live mode / mock mode** | `ANTHROPIC_API_KEY` set -> real agents + `SqliteSaver` (Phase 7). Unset -> all agents mocked + `MemorySaver`. The gate is in `app/api/dependencies.py`. |
| **Effective config** | The per-profile merge of YAML defaults + `user_config` DB overrides (ADR-062), read via `ConfigService.get_effective_config(user_id)`. |
| **Checkpointer** | LangGraph's `SqliteSaver` (the `checkpoints` table in `data/v2.db`). For *resumption only* — UI/history reads go through `workflow_runs`, never the checkpoint. |

---

*This handbook is a living document. When you change an assumption, a startup path, a
migration practice, or a key module, update the relevant spoke and keep this hub's map
accurate. Honor the project's render-verify and wiki-reachability invariants
([CLAUDE.md](../CLAUDE.md)).*
