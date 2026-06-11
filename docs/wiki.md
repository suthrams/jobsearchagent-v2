# Job Search Agent v2 — Wiki

> **Complete documentation index. Every markdown file in the project is listed here exactly once.**
> Organised by topic. v2 documents first, v1 reference at the end.
> If a document is missing from this page, it is missing from the project.

---

## Contents

1. [Project Overview](#1-project-overview)
2. [Getting Started](#2-getting-started)
3. [v2 Architecture — Core Models](#3-v2-architecture--core-models)
4. [v2 Architecture — Agent & Workflow Layer](#4-v2-architecture--agent--workflow-layer)
5. [v2 Architecture — Data, State & Memory](#5-v2-architecture--data-state--memory)
6. [v2 Architecture — Configuration & Prompts](#6-v2-architecture--configuration--prompts)
7. [v2 Architecture — Observability, Security & HITL](#7-v2-architecture--observability-security--hitl)
8. [v2 Architecture — UI, API & Reporting](#8-v2-architecture--ui-api--reporting)
9. [v2 Architecture — Performance, Patterns & Principles](#9-v2-architecture--performance-patterns--principles)
10. [Architecture Decision Records](#10-architecture-decision-records)
11. [Build Phases](#11-build-phases)
12. [Shared libraries from v1 (ADR-063)](#12-shared-libraries-from-v1-adr-063)
13. [Project Maintenance](#13-project-maintenance)

---

## 1. Project Overview

| Document | What it covers |
|---|---|
| [../README.md](../README.md) | Project overview, quick-start, architecture diagram, agent table, cost estimates, tech stack |
| [../CLAUDE.md](../CLAUDE.md) | Development rules for Claude Code: build status, invariants, agent catalogue, running commands, what never to modify |

**Article series (LinkedIn).** The project is documented in a 13-part series on building and operating a real agentic system, from the first overnight script to the finale (also linked from the [README](../README.md#article-series)):

1. [Built an AI Agent to Assist My Job Search: 8 patterns that actually work](https://www.linkedin.com/pulse/built-ai-agent-assist-my-job-search-8-patterns-actually-suthram-xjhye/)
2. [What Building an AI Agent from Scratch Actually Teaches You](https://www.linkedin.com/pulse/what-building-ai-agent-from-scratch-actually-teaches-you-suthram-s8zqe/)
3. [Design Before Code: How a Week Without Coding Changed My AI Agent](https://www.linkedin.com/pulse/design-before-code-how-week-without-coding-changed-my-suthram-7dihe/)
4. [Going multi-agent unlocks 6 new agentic AI patterns](https://www.linkedin.com/pulse/going-multi-agent-unlocks-6-new-agentic-ai-patterns-sivakumar-suthram-ounxe/)
5. [Per-agent model selection: a seam, not a refactor](https://www.linkedin.com/pulse/per-agent-model-selection-seam-refactor-sivakumar-suthram-le2ue/)
6. [Cost is a design decision, not a dashboard](https://www.linkedin.com/pulse/cost-design-decision-dashboard-sivakumar-suthram-xe4oe/)
7. [The agent I trust the least](https://www.linkedin.com/pulse/agent-i-trust-least-sivakumar-suthram-caaje/)
8. [Gate the irreversible, not everything](https://www.linkedin.com/pulse/gate-irreversible-everything-sivakumar-suthram-zjide/)
9. [The model is the only part I cannot pin down](https://www.linkedin.com/pulse/model-only-part-i-cannot-pin-down-sivakumar-suthram-cup8e/)
10. [The strongest security control is the feature you don't build](https://www.linkedin.com/pulse/strongest-security-control-feature-you-dont-build-sivakumar-suthram-8zyue/)
11. [Never trust the green dashboard](https://www.linkedin.com/pulse/never-trust-green-dashboard-sivakumar-suthram-vqh2e/)
12. [Your AI system has more APIs than you think](https://www.linkedin.com/pulse/your-ai-system-has-more-apis-than-you-think-sivakumar-suthram-elnbe/)
13. [AI didn't take me out of the loop. It moved me to the top of it](https://www.linkedin.com/pulse/ai-didnt-take-me-out-loop-moved-top-sivakumar-suthram-zfvfe/) (series finale)

**Build status (from CLAUDE.md):**

| Phase | Description | Status |
|---|---|---|
| 1 | Foundation — schemas, repos, config | ✓ complete |
| 2 | Services — job discovery, resume parser | ✓ complete |
| 3 | LLM provider — ClaudeProvider, prompt caching | ✓ complete |
| 4 | All agents (11 BaseAgent subclasses + 2 LLM helpers = 13 LLM-using components) | ✓ complete |
| 5 | LangGraph workflow orchestrator | ✓ complete |
| 6 | FastAPI backend + Streamlit UI | ✓ complete |
| 7 | Live agents — real Claude, SqliteSaver | ✓ complete |
| 8 | Performance — concurrent scoring + scraping | ✓ complete |
| 9 | Cost optimization — model tiering, volume caps | ✓ complete |
| post-9 | Usability refactor, multi-provider (ADR-053), deep-review-for-all (ADR-054), on-demand tailoring (ADR-055), tailoring page-budget + impact (ADR-056), per-job exclusion (ADR-057), model config to YAML (ADR-058), retire in-graph HITL + human edit (ADR-059), manual scoring selection (ADR-060), configurable funnel width (ADR-061), multi-user profiles (ADR-062), shared v1 libs (ADR-063), per-run search criteria + experience targeting (ADR-064/065), Resume Clinic (ADR-066), resume schema v2 (ADR-067), chat cost caps (ADR-068), PII redaction at the LLM seam (ADR-069), retention + redacted state (ADR-070), per-profile active scoring tracks (ADR-071), tailoring live chat (ADR-072), wired security events + System Dashboard (ADR-073), closed observability gaps + `api_requests` (ADR-074), UI read funnel through the API (ADR-075), runtime budget-cap + failed-call cost + drift proxy (ADR-076/077/078), relevance pre-filter (ADR-079), posting-age staleness (ADR-080), ATS-direct sources (ADR-081), idempotent kickoff (ADR-082), cooperative cancellation (ADR-083), liveness/readiness endpoints (ADR-084), on-demand interview prep (ADR-085), scoring resume projection (ADR-086), async-batch spike (ADR-087, deferred), UI journey reorg + native multipage (ADR-088), Matches live home base (ADR-089), favorites + job-focused Resume Clinic (ADR-090), clinic chat reliability + cost (ADR-091/092), apply-link reliability (ADR-093), clearance exclusion in relevance filter (ADR-094), best-effort dead-link filter (ADR-095), durable run recovery across restarts (ADR-096), curated ATS-direct board batch (ADR-097), per-profile ATS targeting in the Settings UI (ADR-098) | ✓ complete |

**Test count:** ~1031 passing (mock mode, no real API calls in CI). The ADR index and CI are the live source of truth as this drifts.

---

## 2. Getting Started

| Document | What it covers |
|---|---|
| [user_guide.md](user_guide.md) | End-to-end v2 walkthrough — install, configure, start backend + UI, HITL workflow, daily routine, troubleshooting |
| [business_rules.md](business_rules.md) | Plain-language explainability layer — what the system decides and why, by stage (discover/filter/score/select/deep-review/advice/prep/tailor), with execution limits, config rules, HITL/scope boundaries, and privacy rules; each rule cites the enforcing constant/ADR |
| [settings_reference.md](settings_reference.md) | Operator-facing settings catalog — every config setting (search/scoring/scrapers/agents/models/retention), its purpose, and how it changes a run (cost, breadth, strictness, results); includes a "which setting do I change to..." map. Describes effects, not values (cites config_model.md / config.example.yaml for current numbers) |
| [cost_troubleshooting.md](cost_troubleshooting.md) | Step-by-step cost diagnosis: per-agent cost queries, reconciliation against the provider billing console, lever decision matrix, pre-flight estimation, regression-prevention invariants. Read this when cost surprises happen. |
| [model_recommendations.md](model_recommendations.md) | Recommended per-agent model assignment with rationale, estimated cost per run, escalation order if budget pressure mounts, symptoms that signal an agent should be upgraded. Read this when configuring or tuning the system. |
| [features.md](features.md) | Complete v2 feature reference — every agent, out-of-graph human decision points, observability, model tiering, feature summary table |
| [README.md](README.md) | Docs index — maps every topic area to its authoritative file; v1 reference section |

**Execution limits (enforced in `app/workflows/limits.py`):**

The funnel's width is configurable within hard ceilings (ADR-061); the rest are fixed cost guards.

| Limit / config key | Value | Purpose |
|---|---|---|
| `scoring.max_scored` | default 10, ceiling `MAX_SCORED_CEILING` = 25 | How many jobs get research + scoring. In auto mode this is also the discovery cap. Per-run + system-wide configurable (ADR-061) |
| `search.max_discovered` | default/ceiling `MAX_DISCOVERED_JOBS` = 50 | Manual-selection (ADR-060) wide discovery net. Ignored in auto mode |
| `MAX_JOBS_PER_RUN` | 10 | Default value behind `scoring.max_scored` |
| `MAX_SELECTED_JOBS` | 3 | Jobs that auto-qualify for in-graph deep review (lowered from 10 as a cost cut; the human can push more through out-of-graph on-demand) |
| `MAX_RESEARCH_STEPS` | 2 | ReAct loop cap on Research Agent |
| `MAX_REVIEW_ROUNDS` | 2 | Reflection loop cap (lowered from 3 — usually converges by round 2) |
| `MAX_LLM_CALLS_PER_RUN` | 200 | Global run budget — the absolute cost backstop |

---

## 3. v2 Architecture — Core Models

| Document | What it covers |
|---|---|
| [architecture/architecture_overview.md](architecture/architecture_overview.md) | System boundary, 7 system layers, 10 core design principles, input model, core workflows, agentic pattern strategy |
| [architecture/data_model.md](architecture/data_model.md) | All 23 SQLite tables — core, observability (step_executions, agent_events, llm_calls, run_metrics, api_requests), security (security_events), human-decision audit (human_decisions), Resume Clinic (resume_clinic_reviews), lifecycle (idempotency_keys, ADR-082), memory (memory_items), identity (users, ADR-062), favorites (favorite_jobs, ADR-090); per-column data dictionary, per-table workflow usage (who writes / who reads / when), indexing strategy, JSON column conventions, anti-patterns |
| [architecture/state_and_memory_model.md](architecture/state_and_memory_model.md) | WorkflowState schema (22 fields, 9 sections), 6 workflow status values, 15+ step values, state ownership rules, and the long-term-memory DESIGN (the `memory_items` table + write/retrieve patterns are designed but NOT yet wired into the runtime — there is no `MemoryService`) |

---

## 4. v2 Architecture — Agent & Workflow Layer

| Document | What it covers |
|---|---|
| [architecture/agent_model.md](architecture/agent_model.md) | Per-agent input/output contracts, patterns, tools, constraints, observability events; shared rules for every agent; input/output contract standard; prompt structure template |
| [architecture/workflow_model.md](architecture/workflow_model.md) | Complete execution blueprint for all sub-workflows: discovery, resume profile, scoring, shortlist + HITL, deep review, interview prep, tailoring, reporting, error handling; state transition diagrams; parallelization strategy |
| [architecture/relevance_filter_design.md](architecture/relevance_filter_design.md) | Control + data flow for the opt-in reasoning relevance pre-filter (ADR-079): the three-way `scoring_mode_gate`, the wide-net-then-narrow coupling, the redaction seam, the new `RelevanceFilterAgent`, and the never-lose-the-run fallback |
| [architecture/spike_job_data_sources.md](architecture/spike_job_data_sources.md) | Spike (ADR-080 companion): free job-data API alternatives to Adzuna. Aggregator-vs-source-of-truth framing; ATS-direct (Greenhouse/Lever) as the root-cause fix for dead apply links — **prototyped in ADR-081** (`app/services/ats_scrapers.py`); per-company-list tradeoff |

**Agent model tiering (Phase 9):**

| Agent | Model | Pattern | Trigger |
|---|---|---|---|
| Relevance Filter | Haiku | Structured output (batch) | Opt-in, one call before scoring (ADR-079) |
| Research Agent | Haiku | Bounded ReAct | Every job |
| Scoring Agent | Haiku | Structured output | Every job (concurrent) |
| Resume Critic | Haiku | Critique | High-match only |
| Review Auditor | Haiku | Evaluator / Reflection | High-match only |
| Career Advisor | Sonnet | Advisory | Once per run |
| Interview Coach | Sonnet | Conditional | On-demand by default; auto only if `scoring.auto_interview_prep` (ADR-085) |
| Tailoring Agent | Sonnet | Evidence-bound generation | User request |
| Fidelity Reviewer | Haiku | Validation / Guardrail | After every tailoring call |

**Note:** the full set is 13 LLM-using components (11 `BaseAgent` subclasses + 2
utility helpers) — see [agent_graph_overview.md](architecture/agent_graph_overview.md).
Beyond the funnel above, the Resume Clinic adds the Resume Reviewer and Resume Chat
agents (out-of-graph, job-agnostic, ADR-066/068), and `resume_parser` +
`custom_url_extractor` are LLM helpers. Per-agent model assignments are pinned in
`tests/model_pins.json` (the authoritative source, ADR-058); the table above can
drift from the live pins — current critic/auditor are Haiku (cost tuning).

---

## 5. v2 Architecture — Data, State & Memory

| Document | What it covers |
|---|---|
| [architecture/data_model.md](architecture/data_model.md) | 22-table SQLite schema with core, observability (incl. `api_requests`, ADR-074), security, lifecycle (`idempotency_keys`, ADR-082), memory, identity (`users`, ADR-062), and Resume Clinic (`resume_clinic_reviews`, ADR-066) tables. `ResumeProfile` (stored as JSON in `resumes.parsed_profile_json`) was extended in ADR-067 with `gpa`, `honors[]`, and `skill_groups[]`. |
| [architecture/state_and_memory_model.md](architecture/state_and_memory_model.md) | WorkflowState ownership, the long-term-memory design (not yet wired), state update rules, HITL state flow |

**23 SQLite tables at a glance:**

| Category | Tables |
|---|---|
| Core | workflow_runs · jobs · resumes · job_scores · review_rounds · resume_reviews · career_advice · interview_prep · tailored_resumes · reports · human_decisions · user_config |
| Observability | step_executions · agent_events · llm_calls · run_metrics · api_requests (ADR-074) |
| Security | security_events (wired, ADR-073) |
| Lifecycle (ADR-082) | idempotency_keys |
| Resume Clinic (ADR-066) | resume_clinic_reviews |
| Memory | memory_items |
| Identity (ADR-062) | users |
| Favorites (ADR-090) | favorite_jobs |

Profiles (ADR-062): `users` is the identity anchor (id 0 = pre-existing data, new
profiles auto-increment from 1). `resumes`, `memory_items`, `workflow_runs`, and
`user_config` carry a `user_id` so each profile has its own active resume,
isolated memory, history, and config overrides.

---

## 6. v2 Architecture — Configuration & Prompts

| Document | What it covers |
|---|---|
| [architecture/config_model.md](architecture/config_model.md) | Three-layer hybrid config — YAML defaults + DB overrides + locked limits; ConfigService; user-configurable vs locked settings; UI integration |
| [architecture/prompt_and_guardrails_model.md](architecture/prompt_and_guardrails_model.md) | Prompt design philosophy (prompts are architecture); shared guardrails injection into every agent; agent role/task/constraint/schema template; prompt versioning; injection defense |

**Config layers:**

| Layer | Location | Who changes it |
|---|---|---|
| System defaults | `config/config.yaml` | User at setup |
| Runtime overrides | `user_config` SQLite table (per profile, ADR-062) | User via UI |
| Locked limits | `app/workflows/limits.py` | Code only |

Runtime overrides are **per profile** (ADR-062): `get_effective_config(user_id)`
merges a profile's `user_config` rows over the shared YAML defaults. The legacy
`user_id IS NULL` system-wide layer was migrated to profile `"0"`.

---

## 7. v2 Architecture — Observability, Security & HITL

| Document | What it covers |
|---|---|
| [architecture/observability.md](architecture/observability.md) | The observability layer: `ObservabilityService` (one never-crash writer), correlation by `workflow_run_id` (including out-of-graph ops via a lightweight run row), per-call cost/token/latency tracking, the `agent_events` vs `llm_calls` split. WIRED since ADR-073/074: `security_events` (5 emit sites), `human_decisions`, `step_executions`, `api_requests`. Failed-call cost attribution (ADR-077) and the schema-repair drift proxy (ADR-078). All surfaced on the System Dashboard |
| [architecture/security_observability_design.md](architecture/security_observability_design.md) | The System Dashboard design (ADR-073): security events + PSSR + cost in one pane, stored per-run, viewed system-level and profile-scoped; the 5 deterministic security-event emit sites and their PII-safe descriptions |
| [architecture/health_check_design.md](architecture/health_check_design.md) | Implementation design for the liveness/readiness endpoints (ADR-084): `GET /health` + `GET /readyz`, the shared-dependency check registry (database/agent_provider/adzuna/openai), the ready/degraded/down taxonomy, the `api_requests` exclusion, and the System Dashboard health tile |
| [architecture/security.model.md](architecture/security.model.md) | PII minimization — direct identifiers are redacted before any LLM call (ADR-069 seam); untrusted input handling (job descriptions); ethics guardrails (gap labeling, evidence requirement, no fabrication); the wired `security_events` table |
| [architecture/pii_data_flow.md](architecture/pii_data_flow.md) | Where PII flows and where it is stopped (ADR-069): the send-side redaction seam, the at-rest surfaces, and the sanctioned `raw_text` paths |
| [architecture/spike_data_at_rest_security.md](architecture/spike_data_at_rest_security.md) | Data-at-rest security spike (ADR-070): encryption options, the retention/purge cascade, and storing the redacted profile in workflow state |
| [architecture/hitl.md](architecture/hitl.md) | The single HITL pattern (ADR-055/059): the workflow runs end-to-end with NO `interrupt()`; the one human decision is out-of-graph — approve / revise / reject / edit on a tailoring or clinic draft. Backend always validates; the UI never auto-approves |

**One HITL path (ADR-059 retired the in-graph interrupts):**

The workflow no longer pauses mid-graph. Job selection auto-selects qualifying jobs;
the one human decision is tailoring (approve / revise / reject / edit), made
out-of-graph on demand, and a human `edit` is trusted as authored (not re-reviewed).
There is no application / status tracking — that decision point stays human-owned and
intentionally out of scope.

---

## 8. v2 Architecture — UI, API & Reporting

| Document | What it covers |
|---|---|
| [architecture/ui_architecture.md](architecture/ui_architecture.md) | How the Streamlit UI is built: thin entrypoint + views package, the journey navigation (ADR-088 native multipage `st.navigation`/`st.Page`), Matches as the live home base with the auto-refreshing run-status strip (ADR-089), and the SINGLE data path (ADR-075) — every read and write goes through `api_client` -> FastAPI; the direct-SQLite read path and `db_reader.py` were deleted. Reads are cached in `data.py` over `services/reads/`. Companions: `ui_refactor_plan.md`, `ui_read_funnel_implementation_plan.md`, `ui_journey_reorg_plan.md` |
| [architecture/ui_model.md](architecture/ui_model.md) | Streamlit as a thin control surface (not orchestrator); the screens and control responsibilities. NOTE: the "reads direct from SQLite" data-access pattern it describes was superseded by ADR-075 — all reads now go through the API |
| [architecture/api_reference.md](architecture/api_reference.md) | Full HTTP REST API reference — endpoints for starting runs, polling status, submitting HITL decisions, fetching jobs and reports; request/response schemas; error codes; decision types; HITL decision flow |
| [architecture/api_surface_overview.md](architecture/api_surface_overview.md) | One-page visual diagram (PNG + Mermaid) of every REST endpoint grouped by domain, plus a grouped reference table and the two typical user journeys (job-search run + Resume Clinic chat-edit loop) |
| [architecture/agent_graph_overview.md](architecture/agent_graph_overview.md) | One-page visual diagram (PNG + Mermaid) of every LLM-using component grouped by responsibility, plus the in-graph workflow flow, the two out-of-graph operations (tailoring + Resume Clinic), and the cross-cutting invariants every agent observes |
| [architecture/reporting_model.md](architecture/reporting_model.md) | Report structure mapped to agent outputs; answers "is this job right for me, what am I missing, what should I do next"; storage schema; export formats (Markdown, DOCX, PDF) |
| [architecture/ui_refactor_plan.md](architecture/ui_refactor_plan.md) | How the 3.6K-line Streamlit entrypoint was split into a thin entrypoint + views package (the refactor that preceded the read funnel) |
| [architecture/ui_read_funnel_implementation_plan.md](architecture/ui_read_funnel_implementation_plan.md) | The phased plan that routed every UI read through the API and retired `db_reader.py` (ADR-075) |
| [architecture/ui_journey_reorg_plan.md](architecture/ui_journey_reorg_plan.md) | The ADR-088 companion: wireframes, user-engagement workflows, the phased build (Tier 1 nav reorg + merged Matches; Tier 2 Opportunity page), capability-parity checklist, and the Streamlit-vs-alternatives framework evaluation |
| [architecture/favorites_job_focused_clinic_spec.md](architecture/favorites_job_focused_clinic_spec.md) | The ADR-090 companion spec: "My favorite jobs" (a bounded, status-free filter-input) + a job-focused Resume Clinic that reuses the tailoring engine. Data model, API, UI touchpoints, the no-application-tracking boundary, and the test plan |
| [architecture/resume_clinic_strategy.md](architecture/resume_clinic_strategy.md) | Resume Clinic (ADR-066) strategy: job-agnostic resume improvement, the out-of-graph runner, the pluggable role-data provider seam |
| [architecture/resume_clinic_implementation_walkthrough.md](architecture/resume_clinic_implementation_walkthrough.md) | End-to-end implementation walkthrough of the Resume Clinic feature |
| [architecture/resume_clinic_chat_implementation_walkthrough.md](architecture/resume_clinic_chat_implementation_walkthrough.md) | The live-chat + export stack built on top of the clinic (ADR-068/072) |
| [architecture/resume_clinic_chat_visualization.md](architecture/resume_clinic_chat_visualization.md) | Visual map of the Resume Clinic chat-edit flow |

**Key API endpoints:**

| Endpoint | Purpose |
|---|---|
| `POST /workflows` | Start a new workflow run |
| `GET /workflows/{id}` | Poll status and current step |
| `GET /workflows/{id}/jobs` | Scored jobs with filters |
| `GET /workflows/{id}/report` | Final assembled report |
| `POST /tailorings/{id}/decisions` | Record approve / revise / reject / edit on a tailoring draft (the only HITL, out-of-graph; ADR-059) |
| `GET` / `POST /users` | List / create profiles (ADR-062) |
| `POST /users/{id}/resume` | Upload + parse a resume for a profile (ADR-062) |

Identity (ADR-062): every endpoint resolves the acting profile from an optional
`?user_id=` query parameter (defaults to `"0"`).

---

## 9. v2 Architecture — Performance, Patterns & Principles

| Document | What it covers |
|---|---|
| [architecture/performance_scalability.md](architecture/performance_scalability.md) | "Score many cheaply, deeply analyze few" strategy; bounded execution controls (MAX_JOBS_PER_RUN=10, MAX_LLM_CALLS_PER_RUN=200); per-phase performance goals; token/cost controls; scalability anti-patterns. Per-step and per-API latency are now observable on the System Dashboard |
| [architecture/patterns.md](architecture/patterns.md) | 19 agentic AI patterns with full v1→v2 evolution story; v1 foundation (what v1 had and what it couldn't do); per-pattern before/after; pattern strategy (what was proved, changed, and avoided) |
| [architecture/principles.md](architecture/principles.md) | 15 core architecture principles: Backend Owns Intelligence · Controlled Autonomy · Deterministic Where Possible · Bounded Intelligence · State is Source of Truth · Humans Remain in Control · Truthfulness Over Optimization · Separation of Concerns · Observability is Mandatory · Security by Design · Optimize for Iteration · Minimize User Friction · Cost is First-Class Constraint · Prefer Explicit Over Implicit · Build for Evolution |

---

## 10. Architecture Decision Records

Every major design decision in the codebase is captured as an ADR. The canonical list — with current status, supersession history, and a link to each ADR — lives in the index next to the ADR files:

**Full list:** [architecture/adr/ADR-000-index.md](architecture/adr/ADR-000-index.md)

This wiki section used to mirror the index as an inline table. It was dropped to eliminate a duplicate source of truth: every new ADR required updates in two places, nothing enforced the sync, and the inline copy drifted out of date. One list, in one place, next to the files it describes.

---

## 11. Build Phases

**Master plan:** [architecture/implementation_plan.md](architecture/implementation_plan.md) — all 9 phases with deliverables, tests, review gates, and status.

Each phase has a dedicated deep-dive document:

| Phase | Status | Documents |
|---|---|---|
| 1 — Foundation | ✓ complete | [phases/phase-1-foundation.md](architecture/phases/phase-1-foundation.md) · [phases/phase-1-er-diagram.md](architecture/phases/phase-1-er-diagram.md) |
| 2 — Services | ✓ complete | [phases/phase-2-services.md](architecture/phases/phase-2-services.md) |
| 3 — LLM Provider | ✓ complete | [phases/phase-3-llm-provider.md](architecture/phases/phase-3-llm-provider.md) |
| 4 — Agents | ✓ complete | [phases/phase-4-agents.md](architecture/phases/phase-4-agents.md) |
| 5 — Orchestrator | ✓ complete | [phases/phase-5-orchestrator.md](architecture/phases/phase-5-orchestrator.md) |
| 6 — API & UI | ✓ complete | [phases/phase-6-api-ui.md](architecture/phases/phase-6-api-ui.md) |
| 7 — Live Agents | ✓ complete | [phases/phase-7-live-agents.md](architecture/phases/phase-7-live-agents.md) |
| 8 — Performance | ✓ complete | [architecture/implementation_plan.md#phase-8](architecture/implementation_plan.md) |
| 9 — Cost & Hardening | ✓ complete | [architecture/implementation_plan.md#phase-9](architecture/implementation_plan.md) |

Phases 1-8 plus the post-8 work (ADR-053 through ADR-078: multi-provider, on-demand
tailoring, multi-user profiles, Resume Clinic, PII redaction, the System Dashboard,
the UI read funnel, and the observability gap-closing) are all complete. See Section
1's post-9 row for the full list.

**Phase 1 — Foundation:** Defines WorkflowState, 8 agent output schemas, 17-table SQLite schema, ConfigService. The ER diagram shows all table relationships with cardinality.

**Phase 2 — Services:** Builds the deterministic services (JobDiscoveryService, ResumeParser, ObservabilityService, ReportGenerator, ...) — no LLM calls. (The original `SkillNormalizer` and `StatusManager` were later retired in the 2026-06-01 dead-code audit.)

**Phase 3 — LLM Provider:** Establishes ClaudeProvider abstraction (LLMClient interface), PromptLoader with guardrails injection and versioning, retry policy, schema repair loop, ephemeral prompt caching.

**Phase 4 — Agents:** Contracts, prompts, and implementations for the workflow agents; BaseAgent abstraction; observability event patterns; shared constraints.

**Phase 5 — Orchestrator:** LangGraph StateGraph with all workflow nodes, conditional routers, HITL interrupts, reflection loop with stagnation detection, budget enforcement.

**Phase 6 — API & UI:** FastAPI with background thread pool execution and HITL validation; Streamlit dual data-access pattern (writes via API, reads direct from SQLite).

**Phase 7 — Live Agents:** ANTHROPIC_API_KEY gate swaps mocks for real ClaudeProvider + SqliteSaver + real scrapers. Only 3 files change.

**Phase 8 — Performance:** ConcurrentAdzunaScraper (5 workers, ~60s → ~15s); concurrent scoring via ThreadPoolExecutor (5 workers, ~75s → ~20s).

**Phase 9 — Cost Optimization:** Research/Auditor/Fidelity moved Sonnet → Haiku; MAX_JOBS_PER_RUN 20 → 10. Estimated 75–85% cost reduction.

---

## 12. Shared libraries from v1 (ADR-063)

> The v1 runtime was **removed** in ADR-063 — `main.py`, `dashboard.py`,
> `agents/`, `claude/`, `storage/`, `prompts/`, `scrapers/ladders.py`, and
> `models/profile.py` are gone (recoverable from git history). The modules below
> are kept because v2 imports them; their docs describe live code.

| Document | Module (still present) |
|---|---|
| [models/job.md](models/job.md) | `models/job.py` — `Job` / `JobSource` / `SalaryRange`, used by the scrapers |
| [models/config_schema.md](models/config_schema.md) | `models/config_schema.py` — `AdzunaConfig` |
| [models/filters.md](models/filters.md) | `models/filters.py` — shared title/description keyword filters (`EXCLUDED_TITLE_KEYWORDS`, `TECH_DESCRIPTION_KEYWORDS`, `RELEVANT_TITLE_KEYWORDS`) used by `JobDiscoveryService` |
| [scrapers/base.md](scrapers/base.md) | `scrapers/base.py` — abstract base scraper |
| [scrapers/adzuna.md](scrapers/adzuna.md) | `scrapers/adzuna.py` — wrapped by v2 `ConcurrentAdzunaScraper` |
| [scrapers/linkedin.md](scrapers/linkedin.md) | `scrapers/linkedin.py` — built by `app/api/dependencies.py` |

---

## 13. Project Maintenance

| Document | What it covers |
|---|---|
| [../CHANGELOG.md](../CHANGELOG.md) | All notable changes by date — the observability arc (security events + System Dashboard ADR-073, gap-closing + `api_requests` ADR-074, UI read funnel ADR-075, budget-cap/failed-call/drift ADR-076/077/078), Resume Clinic (ADR-066), PII redaction (ADR-069), multi-user profiles (ADR-062), multi-provider (ADR-053), back through Phase 7/8 live agents and performance |
| [dependencies.md](dependencies.md) | All third-party libraries with versions and licence types — v2 stack (langgraph, fastapi, langchain-anthropic, langchain-openai) + shared (anthropic, pydantic, httpx, pdfminer.six) |
| [disclaimer.md](disclaimer.md) | Apache 2.0 terms, no-warranty statement, user responsibility for API costs, scraper compliance notes (Adzuna official, LinkedIn/Ladders grey-area), resume data privacy |
| [../.claude/skills/README.md](../.claude/skills/README.md) | Index for the `.claude/skills/` agent-skills pack — maps each of the 21 pack skills (plus the project-own `smoke-test-ui` and `write-series-article`) to the jobsearchagent-v2 workflow stage where it applies (Claude Code discovers skills only under `.claude/skills/`) |
| [../bugs/README.md](../bugs/README.md) | Root-cause analyses for critical *runtime* bugs (distinct from operational postmortems). Convention + four-section template (`_TEMPLATE.md`), an index table, and one RCA per bug (e.g. `BUG-001` — a dropped `httpx` import in two Streamlit views). Each RCA pairs with a forcing-function test so the same class cannot return silently. |
| [incidents/README.md](incidents/README.md) | Operational postmortem log (distinct from the `bugs/` runtime-RCA log) — convention + entry-shape template, with one postmortem per critical operational issue (e.g. the 2026-05-07 cost-tracking undercount). |

---

## Document Count

| Location | Count |
|---|---|
| Project root (README, CHANGELOG, CLAUDE) | 3 |
| bugs/ (RCA log: README + template + per-bug RCAs) | 3 |
| docs/ top-level | 10 |
| docs/architecture/ | 34 |
| docs/architecture/adr/ | 99 (index + 98 ADRs) |
| docs/architecture/phases/ | 8 |
| docs/incidents/ (postmortem log: README + per-incident) | 2 |
| docs/models/ | 3 |
| docs/scrapers/ | 3 |
| .claude/skills/ (agent-skills pack: 21 SKILL.md + supporting + README, plus project-own smoke-test-ui and write-series-article) | 27 |

---

*Every `.md` file in the project (excluding `.venv/`, `.pytest_cache/`, `.git/`) is listed in this index. The pack ships from [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills) and lives under `.claude/skills/` (where Claude Code discovers it) — see `.claude/skills/README.md` for which skill applies to which jobsearchagent-v2 workflow stage.*
