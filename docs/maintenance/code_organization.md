# Code Organization and Key Modules

> **Type:** Explanation + reference. **Part of:** [Maintainer Handbook](../maintenance.md).
>
> The organizing decisions behind the layout, the **seams** that are expensive to retrofit
> (so don't break them), and a guided tour of the modules that own each concern. Read this
> right after you can run the app. For the full system-layer treatment see
> [architecture/architecture_overview.md](../architecture/architecture_overview.md); the
> durable "why" is in [architecture/principles.md](../architecture/principles.md).

---

## The organizing idea: a one-directional dependency stack

The codebase is layered so that **intelligence, determinism, and persistence never bleed
into each other.** Dependencies point one way: UI -> API -> orchestrator -> (agents +
services) -> repositories -> SQLite. A layer never reaches back up.

```
Streamlit UI        thin control surface; reads/writes ONLY via api_client -> API
      |
FastAPI API         endpoints + dependency wiring; identity seam; observability middleware
      |
Orchestrator        LangGraph StateGraph; the ONLY writer of WorkflowState
    /     \
Agents   Services    LLM (structured output)   |   deterministic (no LLM)
      |
Repositories        SQLite data access (one get_connection per call)
      |
SQLite              data/v2.db (+ checkpoints), data/jobs.db
```

Why this matters to you: when you add a feature, the layer you touch is determined by
*what kind of work it is*. New LLM behavior -> an agent. New deterministic transform -> a
service. New persisted field -> a schema + a repository (+ a migration). New screen -> a
UI view. Putting work in the wrong layer is the most common way to break a seam below.

---

## The load-bearing seams (do not break these)

These are the decisions that are cheap to honor and expensive to retrofit. Each is enforced
by an invariant test or an architectural rule — the 2026-06-13 review confirmed all of them
hold today.

| Seam | Rule | Where | Why it's load-bearing |
|---|---|---|---|
| **UI reads through the API** | The UI never opens the DB; every read and write goes through `api_client` -> FastAPI (ADR-075). | `app/ui/api_client.py`, `app/services/reads/` | A direct-SQLite read path would couple the UI to the schema and bypass auth/observability the moment the app is exposed. Enforced by an invariant test. |
| **Orchestrator-only-mutates-state** | Agents return structured outputs; only the orchestrator writes `WorkflowState`. | `app/workflows/`, `app/state/` | Keeps state changes auditable and in one place; agents stay pure and testable. |
| **Agents depend only on `LLMClient`** | No agent imports a concrete `ClaudeProvider`/`OpenAIProvider`. | `app/providers/llm_client.py`, `app/providers/model_registry.py` | Per-agent model swaps become config, not code (ADR-053/058). |
| **Agents never do I/O** | No agent calls the DB, filesystem, or external URLs directly. | `app/agents/` | Cost, retries, and observability live at the provider seam, not scattered. |
| **Schemas validate every LLM output** | All agent outputs are Pydantic models validated before persistence. | `app/schemas/` | The model is the unreliable part; the schema is the contract that catches drift. |
| **Guardrails on every prompt** | `prompts/shared/guardrails.txt` is prepended to *every* agent prompt by the loader, not by convention. | `app/providers/prompt_loader.py:133-137` | Prompt-injection defense can't be forgotten per-agent. |
| **Redact PII before any LLM call** | Profiles enter an agent only via `redact_pii_for_llm()` / `trim_resume_profile()`. | `app/services/context_trimmer.py` | The send-side privacy boundary (ADR-069); enforced by a source-scan invariant test. |
| **Identity resolves in one place** | `?user_id=` is parsed only in `get_current_user_id`; no router or header parses identity. | `app/api/identity.py` | Adding real auth changes one function body (ADR-062). |

---

## Directory map

```
app/
  api/              FastAPI: endpoints + dependency wiring + middleware
    main.py         app construction, lifespan (drain/recover), observability middleware
    dependencies.py THE Phase 7 gate: live (real agents + SqliteSaver) vs mock deps
    identity.py     get_current_user_id seam (ADR-062)
    routers/        one module per domain (see table below)
  workflows/        LangGraph orchestrator
    workflow_graph.py  build_graph(deps); _instrument_step wraps every node
    graph_state.py     the WorkflowGraphState TypedDict (LangGraph's view of state)
    nodes/             one module per graph node (see table below)
    routers.py         conditional edges (scoring_mode_gate, deep_review_gate, ...)
    run_control.py     process-local single-flight + cooperative-cancel registries
    limits.py          all locked execution limits + the get_* accessors
  agents/           LLM components; all inherit BaseAgent (base_agent.py)
  services/         deterministic, no-LLM work (scraping, parsing, filtering, rendering)
    reads/          read-models that back the UI (workflow_reads, dashboard_reads, ...)
  providers/        LLMClient abstraction + ModelRegistry + prompt_loader
  schemas/          Pydantic output schema per agent + domain types
  state/            WorkflowState schema (the orchestrator's source of truth)
  repositories/     SQLite access; database.py owns the schema + migrations
  prompts/          shared/guardrails.txt + agents/<one per agent>
  ui/               Streamlit native multipage; thin shell + views/ + components/

config/   config.example.yaml + config.yaml (gitignored)
data/     SQLite databases (v2.db, jobs.db) + linkedin_inbox.txt; gitignored
docs/     architecture/ (design + adr/) + the operator docs you are reading
tests/    pytest suite (mock-mode in CI; -m integration for live smoke)
```

---

## Key modules tour

A new maintainer's "what do I open for X" index. Paths are the starting point, not the
whole story — follow the ADR links for the reasoning.

### Entry points and wiring

- **`app/api/main.py`** — builds the FastAPI app, registers routers, installs the
  `@app.middleware("http")` that records every request to `api_requests` (route template
  only, never the raw path — ADR-074), and the `lifespan` that drains in-flight runs on
  shutdown and recovers orphaned runs on startup (ADR-096).
- **`app/api/dependencies.py`** — the most important file to understand wiring. The
  **Phase 7 gate** (`build_and_cache_graph`) chooses live vs mock deps; `_build_real_deps`
  constructs every agent, repository, scraper factory, and the `ModelRegistry`;
  `reload_deps_and_graph` rebuilds them for `POST /config/reload` without a restart.
- **`app/api/identity.py`** — `get_current_user_id`: the single identity seam. No auth
  today (cooperative-trust); the validator is wired to `UserRepository.exists` in live mode.

### The orchestrator

- **`app/workflows/workflow_graph.py`** — `build_graph(deps)` assembles the `StateGraph`.
  `_instrument_step` wraps **every** node with `log_step_started/completed/failed`
  (observability, ADR-074) and the cooperative-cancel check (ADR-083).
- **`app/workflows/nodes/`** — one module per node. The workflow funnel:

  | Node | Role |
  |---|---|
  | `register_run.py` | Graph entry; writes initial state (incl. `effective_config`, `custom_urls`) to `workflow_runs`. |
  | `load_resume.py` | DB-first resume load; stores the **redacted** profile into state (ADR-070). |
  | `discover_jobs.py` | Runs the scrapers (per-run Adzuna + ATS/Workday factories); dedup, optional age/dead-link filters. |
  | `relevance_filter.py` | Opt-in Haiku pre-filter; never-lose-the-run on failure (ADR-079). |
  | `score_jobs.py` | Research + scoring fanned out across a `ThreadPoolExecutor` (5 writers). Reads the LLM-call budget. |
  | `await_scoring_selection.py` / `await_job_selection.py` | Opt-in manual gates; auto-select qualifiers. No `interrupt()`. |
  | `deep_review.py` | Critic + auditor reflection loop on shortlisted jobs. |
  | `career_advice.py` / `interview_prep.py` | Advisory + (conditional) coach. |
  | `generate_report.py` | Terminal node; writes final status + metrics to `workflow_runs`. |

- **`app/workflows/routers.py`** — the conditional edges (e.g. `scoring_mode_gate` routing
  manual / relevance-filter / direct-to-scoring; `deep_review_gate`).
- **`app/workflows/limits.py`** — every locked limit (`MAX_JOBS_PER_RUN`,
  `MAX_LLM_CALLS_PER_RUN`, `MIN_MATCH_SCORE_DEFAULT`, ...) **and** the accessors you must
  use (`get_max_scored`, `get_active_tracks`, `qualifies_for_deep_review`, ...). Read limits
  through these, never inline.
- **`app/workflows/run_control.py`** — the process-local single-flight guard (ADR-082) and
  cooperative cancellation (ADR-083). In-memory; single-process only.

### Agents and providers

- **`app/agents/base_agent.py`** — `BaseAgent`. Every agent sets `AGENT_NAME` (matching its
  prompt file) and splits `_run()` (infrastructure: timing, observability, provider
  dispatch) from `run()` (constructs the Pydantic schema). This split keeps observability
  out of the concrete agents.
- **Concrete agents** — `research_agent`, `scoring_agent`, `relevance_filter_agent`,
  `resume_critic`, `review_auditor`, `career_advisor`, `interview_coach`, `tailoring_agent`,
  `fidelity_reviewer`, plus the Resume Clinic's `resume_reviewer` and `resume_chat`. The
  per-agent contracts are in [architecture/agent_model.md](../architecture/agent_model.md).
- **`app/providers/llm_client.py`** — the `LLMClient` interface agents depend on. Prefer
  `complete_with_usage() -> (dict, LLMUsage)` over the legacy two-step.
- **`app/providers/model_registry.py`** — `ModelRegistry.for_agent(name)` maps each agent to
  its assigned provider+model from config (catalog/pricing/defaults in `config.yaml`).
- **`app/providers/claude_provider.py` / `openai_provider.py`** — concrete providers; shared
  retry policy, schema-repair pass, failed-call cost attribution (ADR-077/078).
- **`app/providers/prompt_loader.py`** — loads agent prompts and prepends the shared
  guardrails to every one.

### Services (deterministic)

- **Discovery:** `job_discovery_service.py`, `concurrent_adzuna_scraper.py`,
  `ats_scrapers.py` (Greenhouse/Lever, ADR-081/097/098), `workday_scraper.py` (ADR-101),
  `custom_url_scraper.py`. Plus the opt-in filters: `experience_filter.py`,
  `posting_age_filter.py`, `dead_link_filter.py`.
- **Privacy + context:** `context_trimmer.py` (`redact_pii_for_llm`, `trim_resume_profile`)
  — the send-side PII seam.
- **Safety:** `url_safety.py` (SSRF redirect re-validation + IP-class rejection); the
  Workday host guard lives in `workday_scraper.py` as the single parse seam.
- **Resume + reporting:** `resume_parser.py`, `report_generator.py`,
  `resume_text_renderer.py` (deterministic, decision-aware export to md/txt/html/json/docx/pdf).
- **Out-of-graph runners:** `scoring_runner.py`, `deep_review_runner.py` — shared by the
  on-demand endpoints so the graph and the ad-hoc ops use the same logic.
- **Config + observability + health:** `config_service.py`, `observability_service.py` (the
  one never-crash writer for all telemetry), `system_health.py`, `readiness.py`.

### Persistence

- **`app/repositories/database.py`** — the schema (`_SCHEMA_SQL`), `init_db()` and its
  migration list, `get_connection()` (WAL + busy_timeout), and `utcnow_iso()` (the single
  timestamp source). Read [schema_and_migrations.md](schema_and_migrations.md) before
  changing it.
- **`*_repository.py`** — one per domain table. Each opens its own connection via
  `get_connection()`. The UI never calls these directly (it goes through the API).

### UI

- **`app/ui/streamlit_app.py`** — the thin entrypoint (native multipage via
  `st.navigation`/`st.Page`, ADR-088).
- **`app/ui/api_client.py`** — the only data path; `set_user_id` is the frontend identity
  seam. **`app/ui/data.py`** caches reads over `app/services/reads/`.
- **`app/ui/views/`** — one module per screen (`render(ctx)`), registered in
  `views/__init__.py` and placed in `nav.NAV_GROUPS` / `nav.DESTINATION_VIEWS`.
  **`app/ui/components/`** — shared widgets (e.g. `resume_chat_panel.py`, `research_panel.py`,
  `favorites.py`). To add a screen, see the recipe in [CLAUDE.md](../../CLAUDE.md).

### Routers (the REST surface)

| Router | Owns |
|---|---|
| `workflows.py` | Kickoff, status, retry, scoring re-entry, cancel; the in-process `_executor` + recovery/drain helpers. |
| `jobs.py` / `reports.py` | Scored-job reads, run reports. |
| `tailoring.py` | On-demand tailoring + deep-review + interview-prep + decisions (the one HITL). |
| `resume_clinic.py` / `review_later.py` | Resume Clinic ops + the review-later store. |
| `config.py` | Effective config reads, `POST /config/reload`, ATS/Workday verify-on-add. |
| `users.py` / `favorites.py` | Profiles (ADR-062) + favorites (ADR-090). |
| `dashboard.py` / `reads.py` | System Dashboard + the UI read funnel. |
| `health.py` / `admin.py` | `/health` + `/readyz`; `/admin/purge` retention trigger. |

---

## Where to make common changes

| You want to... | Touch | Don't forget |
|---|---|---|
| Add a new LLM behavior | a new agent in `app/agents/` + schema + prompt | wire it in `dependencies.py`, add a mock side-effect, pin the model |
| Add a deterministic filter/transform | a service in `app/services/` | call it from the node that owns the stage; never-lose-the-run on failure |
| Persist a new field | schema + repository + a migration in `database.py` | [schema_and_migrations.md](schema_and_migrations.md); update `data_model.md` |
| Add a config knob | `config.example.yaml` + a `get_*` accessor in `limits.py` | read via the accessor, never inline; update `settings_reference.md` |
| Add a screen | a view in `app/ui/views/` + register in nav | the UI must read through `api_client`, never the DB |
| Change a limit | `app/workflows/limits.py` | review the cost impact (PSSR); it's code-only by design |

After any significant change, do the **architecture-docs sweep** ([CLAUDE.md](../../CLAUDE.md)):
grep `docs/architecture/` for the symbols you touched and update every impacted doc, the ADR
index, and `wiki.md`.
