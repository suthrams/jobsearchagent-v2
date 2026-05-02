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
12. [v1 Reference — Entry Points & Diagrams](#12-v1-reference--entry-points--diagrams)
13. [v1 Reference — Agents](#13-v1-reference--agents)
14. [v1 Reference — Claude Layer](#14-v1-reference--claude-layer)
15. [v1 Reference — Models](#15-v1-reference--models)
16. [v1 Reference — Scrapers](#16-v1-reference--scrapers)
17. [v1 Reference — Storage & Prompts](#17-v1-reference--storage--prompts)
18. [Project Maintenance](#18-project-maintenance)

---

## 1. Project Overview

| Document | What it covers |
|---|---|
| [../README.md](../README.md) | Project overview, quick-start, architecture diagram, agent table, cost estimates, tech stack |
| [../CLAUDE.md](../CLAUDE.md) | Development rules for Claude Code: build status, invariants, agent catalogue, running commands, what never to modify |

**Build status (from CLAUDE.md):**

| Phase | Description | Status |
|---|---|---|
| 1 | Foundation — schemas, repos, config | ✓ complete |
| 2 | Services — job discovery, resume parser | ✓ complete |
| 3 | LLM provider — ClaudeProvider, prompt caching | ✓ complete |
| 4 | All 8 agents | ✓ complete |
| 5 | LangGraph workflow orchestrator | ✓ complete |
| 6 | FastAPI backend + Streamlit UI | ✓ complete |
| 7 | Live agents — real Claude, SqliteSaver | ✓ complete |
| 8 | Performance — concurrent scoring + scraping | ✓ complete |
| 9 | Cost optimization — model tiering, volume caps | ⚡ in progress |

**Test count:** 389 passing (mock mode, no real API calls in CI)

---

## 2. Getting Started

| Document | What it covers |
|---|---|
| [user_guide.md](user_guide.md) | End-to-end v2 walkthrough — install, configure, start backend + UI, HITL workflow, daily routine, troubleshooting |
| [features.md](features.md) | Complete v2 feature reference — all 8 agents, HITL checkpoints, observability, model tiering, feature summary table |
| [README.md](README.md) | Docs index — maps every topic area to its authoritative file; v1 reference section |

**Execution limits (enforced in `app/workflows/limits.py`):**

| Limit | Value | Purpose |
|---|---|---|
| `MAX_JOBS_PER_RUN` | 10 | Volume cap — primary cost lever (reduced from 20 in Phase 9) |
| `MAX_SELECTED_JOBS` | 3 | Deep review candidates |
| `MAX_RESEARCH_STEPS` | 2 | ReAct loop cap on Research Agent |
| `MAX_REVIEW_ROUNDS` | 3 | Reflection loop cap |
| `MAX_LLM_CALLS_PER_RUN` | 100 | Global run budget |

---

## 3. v2 Architecture — Core Models

| Document | What it covers |
|---|---|
| [architecture/architecture_overview.md](architecture/architecture_overview.md) | System boundary, 7 system layers, 10 core design principles, input model, core workflows, agentic pattern strategy |
| [architecture/data_model.md](architecture/data_model.md) | All 17 SQLite tables — core (workflow_runs, jobs, job_scores, reviews, advice, tailoring, reports, decisions, user_config), observability (agent_events, llm_calls, run_metrics), security (security_events), memory (memory_items); indexing strategy, JSON column conventions, anti-patterns |
| [architecture/state_and_memory_model.md](architecture/state_and_memory_model.md) | WorkflowState schema (22 fields, 9 sections), 6 workflow status values, 15+ step values, state ownership rules, memory service (memory_items table), memory write/retrieve patterns, anti-patterns |

---

## 4. v2 Architecture — Agent & Workflow Layer

| Document | What it covers |
|---|---|
| [architecture/agent_model.md](architecture/agent_model.md) | Per-agent input/output contracts, patterns, tools, constraints, observability events; shared rules for all 8 agents; input/output contract standard; prompt structure template |
| [architecture/workflow_model.md](architecture/workflow_model.md) | Complete execution blueprint for all sub-workflows: discovery, resume profile, scoring, shortlist + HITL, deep review, interview prep, tailoring, reporting, error handling; state transition diagrams; parallelization strategy |

**Agent model tiering (Phase 9):**

| Agent | Model | Pattern | Trigger |
|---|---|---|---|
| Research Agent | Haiku | Bounded ReAct | Every job |
| Scoring Agent | Haiku | Structured output | Every job (concurrent) |
| Resume Critic | Sonnet | Critique | High-match only |
| Review Auditor | Haiku | Evaluator / Reflection | High-match only |
| Career Advisor | Sonnet | Advisory | Once per run |
| Interview Coach | Sonnet | Conditional | score ≥ 75 or request |
| Tailoring Agent | Sonnet | Evidence-bound generation | User request |
| Fidelity Reviewer | Haiku | Validation / Guardrail | After every tailoring call |

---

## 5. v2 Architecture — Data, State & Memory

| Document | What it covers |
|---|---|
| [architecture/data_model.md](architecture/data_model.md) | 17-table SQLite schema with core, observability, security, and memory tables |
| [architecture/state_and_memory_model.md](architecture/state_and_memory_model.md) | WorkflowState ownership, memory service, state update rules, HITL state flow |

**17 SQLite tables at a glance:**

| Category | Tables |
|---|---|
| Core | workflow_runs · jobs · resumes · job_scores · review_rounds · resume_reviews · career_advice · interview_prep · tailored_resumes · reports · human_decisions · user_config |
| Observability | agent_events · llm_calls · run_metrics |
| Security | security_events |
| Memory | memory_items |

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
| Runtime overrides | `user_config` SQLite table | User via UI |
| Locked limits | `app/workflows/limits.py` | Code only |

---

## 7. v2 Architecture — Observability, Security & HITL

| Document | What it covers |
|---|---|
| [architecture/observability.md](architecture/observability.md) | 6-layer observability stack (workflow, agent, LLM call, tool, HITL, security); correlation by workflow_run_id; per-call cost tracking; ObservabilityService methods; 8 database tables; anti-patterns |
| [architecture/security.model.md](architecture/security.model.md) | PII minimization (parsed profile only, never raw resume text); untrusted input handling (job descriptions); ethics guardrails (gap labeling, evidence requirement, no fabrication); security events table |
| [architecture/hitl.md](architecture/hitl.md) | 7 HITL checkpoints; decision types; state flow (running → waiting_for_user → running); backend responsibilities; frontend constraints; decision validation rules; anti-patterns; end-to-end example |

**7 HITL checkpoints:**

| # | Checkpoint | After |
|---|---|---|
| 1 | Job Selection | Scoring pass |
| 2 | Deep Review Approval | Reflection loop |
| 3 | Interview Prep Decision | Deep review |
| 4 | Tailoring Approval | Tailoring Agent |
| 5 | Fidelity Review Resolution | Fidelity Reviewer |
| 6 | Report Export Approval | Report generation |
| 7 | Application Status Update | User decision |

---

## 8. v2 Architecture — UI, API & Reporting

| Document | What it covers |
|---|---|
| [architecture/ui_model.md](architecture/ui_model.md) | Streamlit as thin control surface (not orchestrator); dual data-access pattern (writes via FastAPI, reads direct from SQLite); 12 screens including Active Run sections, settings, job ranking, deep review, HITL controls, report viewing |
| [architecture/api_reference.md](architecture/api_reference.md) | Full HTTP REST API reference — endpoints for starting runs, polling status, submitting HITL decisions, fetching jobs and reports; request/response schemas; error codes; decision types; HITL decision flow |
| [architecture/reporting_model.md](architecture/reporting_model.md) | Report structure mapped to agent outputs; answers "is this job right for me, what am I missing, what should I do next"; storage schema; export formats (Markdown, DOCX, PDF) |

**Key API endpoints:**

| Endpoint | Purpose |
|---|---|
| `POST /workflows` | Start a new workflow run |
| `GET /workflows/{id}` | Poll status and current step |
| `POST /workflows/{id}/decisions` | Submit HITL decision to resume |
| `GET /workflows/{id}/jobs` | Scored jobs with filters |
| `GET /workflows/{id}/report` | Final assembled report |

---

## 9. v2 Architecture — Performance, Patterns & Principles

| Document | What it covers |
|---|---|
| [architecture/performance_scalability.md](architecture/performance_scalability.md) | "Score many cheaply, deeply analyze few" strategy; bounded execution controls (MAX_JOBS=10, MAX_LLM_CALLS=100); per-phase performance goals; token/cost controls; scalability anti-patterns |
| [architecture/patterns.md](architecture/patterns.md) | 19 agentic AI patterns with full v1→v2 evolution story; v1 foundation (what v1 had and what it couldn't do); per-pattern before/after; pattern strategy (what was proved, changed, and avoided) |
| [architecture/principles.md](architecture/principles.md) | 15 core architecture principles: Backend Owns Intelligence · Controlled Autonomy · Deterministic Where Possible · Bounded Intelligence · State is Source of Truth · Humans Remain in Control · Truthfulness Over Optimization · Separation of Concerns · Observability is Mandatory · Security by Design · Optimize for Iteration · Minimize User Friction · Cost is First-Class Constraint · Prefer Explicit Over Implicit · Build for Evolution |

---

## 10. Architecture Decision Records

52 ADRs covering every major design decision. All accepted.

**Index:** [architecture/adr/ADR-000-index.md](architecture/adr/ADR-000-index.md)

| ADR | Decision | Phase |
|---|---|---|
| [001](architecture/adr/ADR-001-keep-v1-stable-and-use-v2-for-refactor.md) | Keep v1 stable; develop v2 in parallel | Foundation |
| [002](architecture/adr/ADR-002-orchestrator-mediated-agent-coordination-with-shared-state.md) | Orchestrator-mediated agent coordination with shared state | Foundation |
| [003](architecture/adr/ADR-003-separate-frontend-and-backend-responsibilities.md) | Separate frontend and backend responsibilities | Foundation |
| [004](architecture/adr/ADR-004-backend-owns-workflow-orchestration.md) | Backend owns workflow orchestration | Foundation |
| [005](architecture/adr/ADR-005-use-specialized-agents.md) | Use specialized agents — one responsibility each | Agents |
| [006](architecture/adr/ADR-006-keep-deterministic-work-in-tools-and-services.md) | Keep deterministic work in tools and services | Services |
| [007](architecture/adr/ADR-007-use-structured-output-schemas.md) | Use structured output schemas (Pydantic) | Agents |
| [008](architecture/adr/ADR-008-use-bounded-reflection-for-resume-critique.md) | Use bounded reflection for resume critique | Agents |
| [009](architecture/adr/ADR-009-do-not-use-formal-multi-agent-protocol-for-mvp.md) | No formal multi-agent protocol for MVP | Foundation |
| [010](architecture/adr/ADR-010-use-react-selectively-in-research-agent-only.md) | Use ReAct selectively in Research Agent only | Agents |
| [011](architecture/adr/ADR-011-human-in-the-loop-as-backend-workflow-pauses.md) | Human-in-the-loop as backend workflow pauses | Orchestrator |
| [012](architecture/adr/ADR-012-deep-review-only-on-shortlisted-jobs.md) | Deep review only on shortlisted jobs | Orchestrator |
| [013](architecture/adr/ADR-013-separate-resume-gaps-from-career-gaps.md) | Separate resume gaps from career gaps | Agents |
| [014](architecture/adr/ADR-014-interview-coach-is-conditional.md) | Interview Coach is conditional | Agents |
| [015](architecture/adr/ADR-015-tailoring-must-be-evidence-bound.md) | Tailoring must be evidence-bound | Agents |
| [016](architecture/adr/ADR-016-add-fidelity-reviewer-after-tailoring-agent.md) | Add Fidelity Reviewer after Tailoring Agent | Agents |
| [017](architecture/adr/ADR-017-ethical-ai-use-for-career-decision-support.md) | Ethical AI use for career decision support | Foundation |
| [018](architecture/adr/ADR-018-global-ethics-guardrails-must-be-included-in-agent-prompts.md) | Global ethics guardrails in every agent prompt | Agents |
| [019](architecture/adr/ADR-019-treat-scraped-job-descriptions-as-untrusted-input.md) | Treat scraped job descriptions as untrusted input | Security |
| [020](architecture/adr/ADR-020-minimize-pii-sent-to-llms.md) | Minimize PII sent to LLMs | Security |
| [021](architecture/adr/ADR-021-store-workflow-runs-not-just-final-results.md) | Store workflow runs, not just final results | Data |
| [022](architecture/adr/ADR-022-use-json-columns-for-evolving-agent-outputs.md) | Use JSON columns for evolving agent outputs | Data |
| [023](architecture/adr/ADR-023-make-observability-first-class.md) | Make observability first-class | Observability |
| [024](architecture/adr/ADR-024-track-prompt-versions.md) | Track prompt versions | Observability |
| [025](architecture/adr/ADR-025-add-security-and-policy-layer-around-agents-and-tools.md) | Add security and policy layer around agents and tools | Security |
| [026](architecture/adr/ADR-026-track-security-events.md) | Track security events | Security |
| [027](architecture/adr/ADR-027-add-cost-token-and-latency-tracking.md) | Add cost, token, and latency tracking | Observability |
| [028](architecture/adr/ADR-028-start-with-streamlit-and-sqlite-mvp.md) | Start with Streamlit + SQLite MVP | UI |
| [029](architecture/adr/ADR-029-add-fastapi-only-after-service-layer-stabilizes.md) | Add FastAPI only after service layer stabilizes | API |
| [030](architecture/adr/ADR-030-use-skillsyaml-for-application-skill-taxonomy.md) | Use skills.yaml for application skill taxonomy | Services |
| [031](architecture/adr/ADR-031-separate-claude-code-support-files-from-app-code.md) | Separate Claude Code support files from app code | Foundation |
| [032](architecture/adr/ADR-032-abstract-llm-providers.md) | Abstract LLM providers | Provider |
| [033](architecture/adr/ADR-033-status-manager-must-be-non-ai.md) | Status Manager must be non-AI | Services |
| [034](architecture/adr/ADR-034-do-not-overbuild-before-proving-core-workflow.md) | Do not overbuild before proving core workflow | Foundation |
| [035](architecture/adr/ADR-035-enforce-a-structured-workflow-state-schema.md) | Enforce a structured WorkflowState schema | Orchestrator |
| [036](architecture/adr/ADR-036-define-explicit-agent-input-and-output-contracts.md) | Define explicit agent input/output contracts | Agents |
| [037](architecture/adr/ADR-037-standard-failure-and-retry-strategy.md) | Standard failure and retry strategy | Provider |
| [038](architecture/adr/ADR-038-version-prompts-agents-schemas-and-workflows.md) | Version prompts, agents, schemas, and workflows | Provider |
| [039](architecture/adr/ADR-039-define-sequential-mvp-execution-model-with-future-parallelism.md) | Sequential MVP execution model with future parallelism | Orchestrator |
| [040](architecture/adr/ADR-040-define-data-retention-and-privacy-policy.md) | Define data retention and privacy policy | Data |
| [041](architecture/adr/ADR-041-all-agent-execution-must-be-bounded.md) | All agent execution must be bounded | Agents |
| [042](architecture/adr/ADR-042-define-testing-and-evaluation-strategy.md) | Define testing and evaluation strategy | Testing |
| [043](architecture/adr/ADR-043-define-prompt-evaluation-and-regression-strategy.md) | Define prompt evaluation and regression strategy | Testing |
| [044](architecture/adr/ADR-044-define-v1-to-v2-migration-strategy.md) | Define v1 to v2 migration strategy | Foundation |
| [045](architecture/adr/ADR-045-Job-Intake-Supports-Automated-Discovery-and-Manual-Input.md) | Job intake supports automated discovery and manual input | Services |
| [046](architecture/adr/ADR-046-Hybrid_Configuration_Model_YAML_And_DB_Overrides.md) | Hybrid configuration model — YAML + DB overrides | Config |
| [047](architecture/adr/ADR-047-use-sqlitesaver-for-workflow-checkpoint-persistence.md) | Use SqliteSaver for LangGraph checkpoint persistence | Phase 7 |
| [048](architecture/adr/ADR-048-api-key-presence-as-live-mock-mode-gate.md) | API key presence as live/mock mode gate | Phase 7 |
| [049](architecture/adr/ADR-049-use-threadpoolexecutor-for-concurrent-job-scoring.md) | Use ThreadPoolExecutor for concurrent job scoring | Phase 8 |
| [050](architecture/adr/ADR-050-wrap-v1-adzuna-scraper-with-concurrent-adapter.md) | Wrap v1 AdzunaScraper with a concurrent adapter | Phase 8 |
| [051](architecture/adr/ADR-051-tiered-model-assignment-haiku-for-volume-sonnet-for-generative.md) | Tiered model assignment — Haiku for volume/validation, Sonnet for generative | Phase 9 |
| [052](architecture/adr/ADR-052-reduce-max-jobs-per-run-as-cost-control.md) | Reduce MAX_JOBS_PER_RUN as primary cost control lever | Phase 9 |

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
| 9 — Cost & Hardening | ⚡ in progress | [architecture/implementation_plan.md#phase-9](architecture/implementation_plan.md) |

**Phase 1 — Foundation:** Defines WorkflowState, 8 agent output schemas, 17-table SQLite schema, ConfigService. The ER diagram shows all table relationships with cardinality.

**Phase 2 — Services:** Builds 6 deterministic services (JobDiscoveryService, ResumeParser, SkillNormalizer, StatusManager, ObservabilityService, ReportGenerator) — no LLM calls.

**Phase 3 — LLM Provider:** Establishes ClaudeProvider abstraction (LLMClient interface), PromptLoader with guardrails injection and versioning, retry policy, schema repair loop, ephemeral prompt caching.

**Phase 4 — Agents:** Contracts, prompts, and implementations for all 8 agents; BaseAgent abstraction; observability event patterns; shared constraints.

**Phase 5 — Orchestrator:** LangGraph StateGraph with all workflow nodes, conditional routers, HITL interrupts, reflection loop with stagnation detection, budget enforcement.

**Phase 6 — API & UI:** FastAPI with background thread pool execution and HITL validation; Streamlit dual data-access pattern (writes via API, reads direct from SQLite).

**Phase 7 — Live Agents:** ANTHROPIC_API_KEY gate swaps mocks for real ClaudeProvider + SqliteSaver + real scrapers. Only 3 files change.

**Phase 8 — Performance:** ConcurrentAdzunaScraper (5 workers, ~60s → ~15s); concurrent scoring via ThreadPoolExecutor (5 workers, ~75s → ~20s).

**Phase 9 — Cost Optimization:** Research/Auditor/Fidelity moved Sonnet → Haiku; MAX_JOBS_PER_RUN 20 → 10. Estimated 75–85% cost reduction.

---

## 12. v1 Reference — Entry Points & Diagrams

> v1 (`main.py`, `agents/`, `scrapers/`, `storage/`, `dashboard.py`) remains stable and runnable. These documents are accurate for v1 code — they do not describe v2.

| Document | What it covers |
|---|---|
| [main.md](main.md) | `main.py` v1 CLI — `python main.py`, `--list`, `--tailor`, `--purge`, `--dashboard` commands; scrape/score/display flow |
| [dashboard.md](dashboard.md) | `dashboard.py` v1 Streamlit dashboard — 7 views, sidebar controls, job cards, run history, score badges |
| [architecture.md](architecture.md) | v1 architecture Mermaid diagrams — solution overview, system layers, main run flow, cache-aside, batched fan-out, state machine, tailoring sequence, prompt-as-template, pre-filter gate, agentic patterns mind map |

---

## 13. v1 Reference — Agents

| Document | What it covers |
|---|---|
| [agents/profile_agent.md](agents/profile_agent.md) | `ProfileAgent` — PDF → structured Profile; cache-aside pattern (file mtime); pdfplumber extraction |
| [agents/scoring_agent.md](agents/scoring_agent.md) | `ScoringAgent` — batch scoring (10 jobs/call); 3 parallel calls via ThreadPoolExecutor; prompt caching; MIN_PERSIST_SCORE filter |
| [agents/tailoring_agent.md](agents/tailoring_agent.md) | `TailoringAgent` — single-pass resume section rewriting; output saved to `output/resumes/` |

---

## 14. v1 Reference — Claude Layer

| Document | What it covers |
|---|---|
| [claude/client.md](claude/client.md) | `ClaudeClient` — Anthropic SDK wrapper; all API calls routed here; operation-based settings (model, max_tokens, temperature per operation) |
| [claude/prompt_loader.md](claude/prompt_loader.md) | `PromptLoader` — loads prompt template files, renders with keyword substitution |
| [claude/response_parser.md](claude/response_parser.md) | `ResponseParser` — strips code fences, extracts JSON, validates against Pydantic schema |

---

## 15. v1 Reference — Models

| Document | What it covers |
|---|---|
| [models/job.md](models/job.md) | `Job` data model — all fields, lifecycle enums (ApplicationStatus, CareerTrack), `TrackScores` and `TrackScore` structure, `is_stale` property |
| [models/profile.md](models/profile.md) | `Profile` model — ExperienceEntry, EducationEntry, CertificationEntry, computed `total_years_experience` |
| [models/config_schema.md](models/config_schema.md) | Pydantic schema for `config.yaml` — all sections, defaults, validation rules |
| [models/filters.md](models/filters.md) | Shared filter keyword lists — `EXCLUDED_TITLE_KEYWORDS`, `TECH_DESCRIPTION_KEYWORDS`, `RELEVANT_TITLE_KEYWORDS`; US state extraction logic (shared by v1 and v2) |

---

## 16. v1 Reference — Scrapers

| Document | What it covers |
|---|---|
| [scrapers/base.md](scrapers/base.md) | Abstract base scraper — `scrape()` interface, deduplication contract |
| [scrapers/adzuna.md](scrapers/adzuna.md) | `AdzunaScraper` — Adzuna REST API; title × location search matrix; remote keyword searches; rate limiting |
| [scrapers/linkedin.md](scrapers/linkedin.md) | `LinkedInScraper` — manual URL intake from `inbox/linkedin.txt`; fetch and clear pattern |
| [scrapers/ladders.md](scrapers/ladders.md) | `LaddersScraper` — Ladders.com HTML scraping; $100k+ role focus |

---

## 17. v1 Reference — Storage & Prompts

| Document | What it covers |
|---|---|
| [storage/db.md](storage/db.md) | v1 SQLite schema — `jobs` table with all columns, CRUD operations, `backfill_states()`, `delete_below_threshold()` |
| [prompts/overview.md](prompts/overview.md) | v1 prompt system overview — template format, variable substitution, prompt file organisation |

**v1 prompt files** (live in `prompts/` at project root — these are the actual prompt templates):

| File | What it covers |
|---|---|
| [../prompts/parse_resume.md](../prompts/parse_resume.md) | v1 resume extraction prompt — extracts name, headline, experience, skills, education, certifications as JSON |
| [../prompts/score_job.md](../prompts/score_job.md) | v1 batch scoring prompt — scores IC/Architect/Management tracks (0–100) with summary and recommendation; injection defense built in |
| [../prompts/tailor_resume.md](../prompts/tailor_resume.md) | v1 resume tailoring prompt — rewrites professional summary and experience bullets for a specific job and track; gaps analysis |

---

## 18. Project Maintenance

| Document | What it covers |
|---|---|
| [../CHANGELOG.md](../CHANGELOG.md) | All notable changes by date — Phase 9 cost optimization, Phase 7/8 live agents and performance, v1 dashboard fixes, Python 3.12 compatibility |
| [dependencies.md](dependencies.md) | All third-party libraries with versions and licence types — v2 stack (langgraph, fastapi, langchain-anthropic) + v1 shared (anthropic, pydantic, httpx, pdfplumber) |
| [disclaimer.md](disclaimer.md) | Apache 2.0 terms, no-warranty statement, user responsibility for API costs, scraper compliance notes (Adzuna official, LinkedIn/Ladders grey-area), resume data privacy |
| [blog_draft_patterns_v2.md](blog_draft_patterns_v2.md) | Draft blog post on agentic AI patterns used in v2 — for external publication |

---

## Document Count

| Location | Count |
|---|---|
| Project root (README, CHANGELOG, CLAUDE) | 3 |
| docs/ top-level | 9 |
| docs/architecture/ | 17 |
| docs/architecture/adr/ | 53 (index + 52 ADRs) |
| docs/architecture/phases/ | 8 |
| docs/agents/ | 3 |
| docs/claude/ | 3 |
| docs/models/ | 4 |
| docs/scrapers/ | 4 |
| docs/storage/ | 1 |
| docs/prompts/ | 1 |
| prompts/ (project root) | 3 |
| **Total** | **110** |

---

*Every `.md` file in the project (excluding `.venv/`, `.pytest_cache/`, `.git/`) is listed in this index. The wiki itself (`docs/wiki.md`) is the 110th file.*
