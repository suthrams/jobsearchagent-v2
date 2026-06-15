# Running and Operating

> **Type:** How-to + reference. **Part of:** [Maintainer Handbook](../maintenance.md).
>
> How to start the system, configure its environment, switch between live and mock mode,
> and apply config changes without a restart. For the end-user walkthrough (profiles,
> searches, reports) see [user_guide.md](../user_guide.md).

---

## Prerequisites

- Python with the project dependencies installed (see [dependencies.md](../dependencies.md)).
- A `.env` file or exported environment variables (next section).
- Two processes: the FastAPI **backend** and the Streamlit **UI**. Start the backend first.

---

## Environment variables

| Variable | Required? | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | For live mode | **The mode gate.** Set -> real Claude agents + `SqliteSaver` + real scrapers. Unset -> all agents mocked + `MemorySaver` (no API calls). |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | For Adzuna discovery | Enables the Adzuna scraper. Absent -> Adzuna is skipped, other sources still run. |
| `OPENAI_API_KEY` | Optional | Enables OpenAI models in the `ModelRegistry`. Absent -> OpenAI models are unregistered and hidden in Settings; workflows continue on Claude. |
| `WORKFLOW_SHUTDOWN_DRAIN_SECONDS` | Optional (default `30`) | How long graceful shutdown waits for in-flight runs to drain before exiting (ADR-096). |
| `WEB_CONCURRENCY` / `--workers` | **Leave at 1** | More than one worker breaks the single-process invariants. As of ADR-106 the startup guard **refuses to boot** if it detects `>1` (or a non-loopback `--host`). See [persistence_and_concurrency.md](persistence_and_concurrency.md). |
| `ALLOW_UNSAFE_DEPLOYMENT` | Optional (default off) | Escape hatch for the ADR-106 guard: truthy (`1/true/yes/on`) downgrades the hard refusal to a logged warning so an operator who accepts the double-spend / cross-tenant risk can proceed. Leave unset for the normal single-user run. |

Secret hygiene: never commit a real key. Run the secret audit before every commit
(`tools/check_no_secrets.sh`).

---

## Start the backend and UI

```bash
# 1. Backend (FastAPI). Live-agent mode when ANTHROPIC_API_KEY is set.
uvicorn app.api.main:app --reload

# 2. UI (Streamlit), in a second terminal.
streamlit run app/ui/streamlit_app.py
```

Open `http://localhost:8501` for the UI. The backend serves on `http://localhost:8000`.

> **Windows: start servers via PowerShell `Start-Process`, not a Bash `&` background
> launch.** A Bash `&` launch can leave an orphaned uvicorn (and its multiprocessing-fork
> child) holding port `8000` and serving **stale code** after you think you restarted. The
> fix and the diagnosis are in
> [backup_restore_and_troubleshooting.md](backup_restore_and_troubleshooting.md).

---

## Live mode vs mock mode (the Phase 7 gate)

The gate lives in `app/api/dependencies.py::build_and_cache_graph`, called once at startup:

- **`ANTHROPIC_API_KEY` set** -> `_build_real_deps()`: real `ClaudeProvider` agents wired
  through `ModelRegistry`, real scrapers, and a `SqliteSaver` checkpointer over
  `data/v2.db`. `init_db()` runs here (idempotent `CREATE TABLE IF NOT EXISTS` + migrations).
- **Not set** -> `_build_mocked_deps()`: every agent is a `MagicMock` with a deterministic
  `side_effect`, and a `MemorySaver` (in-memory, non-persistent) checkpointer. This is the
  Phase 6 behavior and the mode the test suite runs in — **no real API calls, no spend.**

Mock mode is the right way to develop and demo the UI and workflow shape without cost. Live
mode is required to see real agent outputs and to exercise persistence.

The mock side-effects are real Pydantic schema instances (see `_make_*_side_effect` in
`dependencies.py`), so the orchestration, persistence glue, and UI all behave the same —
only the *content* is canned.

---

## Health and readiness

Two **unauthenticated** endpoints (the only ones with no `?user_id=`), excluded from the
`api_requests` telemetry so probes don't flood it (ADR-084):

| Endpoint | Meaning |
|---|---|
| `GET /health` | Liveness — the process is up. |
| `GET /readyz` | Readiness — probes shared dependencies via `app/services/readiness.py`. `database` critical (down -> `503`); `agent_provider` / `adzuna` capabilities -> `degraded` (200); `openai` optional. Secret-safe: reports presence/mode only, never key values. |

The System Dashboard surfaces `/readyz` live on its "System health" tile. Don't synthetically
probe individual routes to check health — most mutate.

---

## Startup and shutdown lifecycle (ADR-096 durable run recovery)

**First, the deployment guard (ADR-106).** The lifespan calls
`enforce_deployment_safety()` (`app/api/deployment_guard.py`) *before* any wiring. It
detects a multi-worker (`WEB_CONCURRENCY>1` / `--workers N`) or non-loopback (`--host`)
launch from `sys.argv` + env and **refuses to start** (raising `UnsafeDeploymentError`),
unless `ALLOW_UNSAFE_DEPLOYMENT` is truthy (then it logs a prominent warning and
continues). It is skipped under the test seam (`dependency_overrides`). Best-effort: it
will not catch a programmatic `uvicorn.run(host=...)` or a gunicorn config-file launch.

The FastAPI `lifespan` (`app/api/main.py`) then wires three layers so a process death
mid-run does not silently freeze a run at `running`:

- **On startup:** `_recover_orphaned_runs_on_startup()` -> `recover_orphaned_runs(...)`
  re-submits checkpointed-but-unfinished runs via the graph (`graph.invoke(None, config)`),
  bounded by `MAX_RESUME_ATTEMPTS=3` (counter in `state_json.resume_attempts`).
- **On shutdown:** `_drain_inflight_runs_on_shutdown()` -> `drain_inflight_runs(timeout)`
  waits up to `WORKFLOW_SHUTDOWN_DRAIN_SECONDS` (default 30) for in-flight runs to finish.
  It intentionally does **not** shut down `_executor` (that would break test-harness
  lifespan reuse; process exit tears it down anyway).
- **Backstop:** `WorkflowRepository.reconcile_orphaned_runs()` fails-everything that is
  still stuck.

All runs submit through `_submit_run` so the drain can track them. This is single-process
by design — a multi-worker rollout needs a shared registry (roadmap item 4).

---

## Applying config changes

Config is two layers: `config/config.yaml` defaults overlaid by per-profile `user_config`
DB rows (ADR-062), read via `ConfigService.get_effective_config(user_id)`. How a change
takes effect depends on *what* changed:

| Change | How it applies |
|---|---|
| **Search / scoring / scrapers settings** (`search.*`, `scoring.*`, `scrapers.*`) | **Next run.** Resolved per-run from the effective config — no reload needed. ATS/Workday company lists are per-profile and resolved per run (ADR-098/101). |
| **Per-agent provider/model assignment** (`agents.*`, `models.*`) | `POST /config/reload` rebuilds the `ModelRegistry` + graph without a restart (ADR-053 addendum). In-flight runs keep the old assignment; only new runs use the new one. |
| **Prompt files** (`prompts/`) | **Restart required** — `PromptLoader` caches files at first read. |
| **Code changes** | Restart (or rely on `--reload` in dev). |
| **Locked limits** (`app/workflows/limits.py`) | Code change only — these are intentionally not user-configurable. |

`reload_deps_and_graph()` (the `POST /config/reload` handler) is careful about ordering: it
builds the new graph fully before releasing the old `SqliteSaver`, so there is never a
window where `get_graph()` returns `None`.

---

## Day-2 operations quick links

- **Cost surprised you:** [cost_troubleshooting.md](../cost_troubleshooting.md) (diagnosis)
  + [model_recommendations.md](../model_recommendations.md) (which model to move).
- **Which knob changes behavior X:** [settings_reference.md](../settings_reference.md).
- **A run is stuck / port in use / stale code:**
  [backup_restore_and_troubleshooting.md](backup_restore_and_troubleshooting.md).
- **Back up or restore the data:**
  [backup_restore_and_troubleshooting.md](backup_restore_and_troubleshooting.md).
- **Retention / purge:** `POST /admin/purge`, `tools/purge_data.py`, or the Settings
  control. Explicit-trigger-only — never automatic (ADR-070).
