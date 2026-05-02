# Job Search Agent v2 — Wiki

> **Landing page for all project documentation.**
> Start here. Every section links to the authoritative detail file.

---

## Contents

1. [Project Overview](#1-project-overview)
2. [Build Status](#2-build-status)
3. [Running the System](#3-running-the-system)
4. [Architecture](#4-architecture)
5. [Agents](#5-agents)
6. [Data Model](#6-data-model)
7. [State & Memory](#7-state--memory)
8. [Workflow Model](#8-workflow-model)
9. [Configuration](#9-configuration)
10. [Observability](#10-observability)
11. [Security & Ethics](#11-security--ethics)
12. [Human-in-the-Loop](#12-human-in-the-loop)
13. [Design Patterns](#13-design-patterns)
14. [Architecture Principles](#14-architecture-principles)
15. [Architecture Decision Records](#15-architecture-decision-records)
16. [Phase History](#16-phase-history)
17. [Testing Strategy](#17-testing-strategy)
18. [Migration from v1](#18-migration-from-v1)
19. [Dependencies & Licences](#19-dependencies--licences)
20. [Changelog](#20-changelog)

---

## 1. Project Overview

Job Search Agent v2 is a multi-agent career intelligence system that:

- **Discovers** jobs from Adzuna (aggregates Indeed, Glassdoor, etc.) and LinkedIn (manual URL intake) — concurrently
- **Filters** noise with keyword gates before spending any API tokens
- **Researches** each company with a bounded ReAct agent — culture, tech signals, risk flags
- **Scores** each job against your resume across three career tracks (IC, Architect, Management) — concurrently
- **Reviews** high-match jobs with a critic → auditor reflection loop
- **Advises** on career positioning after the scoring pass
- **Coaches** interview preparation for roles above the match threshold
- **Tailors** your resume with evidence-bound generation and a fidelity guardrail
- **Tracks** every decision, reasoning step, and cost in SQLite

**Career tracks**

| Track | Target Roles |
|---|---|
| `ic` | Senior / Staff / Principal Engineer |
| `architect` | Solutions / Principal / Enterprise Architect |
| `management` | Senior Manager / Director / Head of Engineering / VP |

**References:** [README.md](../README.md) · [docs/features.md](features.md) · [docs/user_guide.md](user_guide.md)

---

## 2. Build Status

| Phase | Description | Status |
|---|---|---|
| 1 | Foundation — schemas, repositories, config | ✓ complete |
| 2 | Services — job discovery, resume parser, observability | ✓ complete |
| 3 | LLM provider — ClaudeProvider, PromptLoader, prompt caching | ✓ complete |
| 4 | All 8 agents | ✓ complete |
| 5 | LangGraph workflow orchestrator | ✓ complete |
| 6 | FastAPI backend + Streamlit UI | ✓ complete |
| 7 | Live agents — real Claude calls, SqliteSaver, real scrapers | ✓ complete |
| 8 | Performance — concurrent scoring + concurrent scraping | ✓ complete |
| 9 | Cost optimization — model tiering, volume caps | ⚡ in progress |

**Test count:** 389 passing (mock mode, no real API calls)

**Detail:** [implementation_plan.md](architecture/implementation_plan.md)

---

## 3. Running the System

### Prerequisites

- Python 3.11+
- `ANTHROPIC_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` in `.env`
- `resume.pdf` in the project root
- `config/config.yaml` copied from `config/config.example.yaml` and edited

### Commands

```bash
# Start the FastAPI backend (live-agent mode when ANTHROPIC_API_KEY is set)
uvicorn app.api.main:app --reload

# Start the Streamlit UI (separate terminal)
streamlit run app/ui/streamlit_app.py

# Run the full test suite (mock mode — no real API calls)
python -m pytest tests/

# Run live-API smoke tests
python -m pytest tests/ -m integration
```

**Mock mode:** If `ANTHROPIC_API_KEY` is absent, the backend auto-starts in mock mode — all agents return fixture responses. Useful for UI development and CI.

**References:** [docs/user_guide.md](user_guide.md) · [README.md](../README.md)

---

## 4. Architecture

### System Layers

```
UI (Streamlit)
  ↓
API (FastAPI + Uvicorn)
  ↓
Workflow Orchestrator (LangGraph + SqliteSaver)
  ↓
Agents (8 specialized) + Services (deterministic)
  ↓
LLM Provider (ClaudeProvider → ChatAnthropic)
  ↓
Persistence (SQLite — raw sqlite3)
```

### Core Design Principle

> Score many → Deeply analyze few.

All 10 jobs are researched and scored. Only shortlisted jobs (≤ 3) receive deep review, interview prep, and tailoring.

### Key Invariants

| Limit | Value |
|---|---|
| MAX_JOBS_PER_RUN | 10 |
| MAX_SELECTED_JOBS | 3 |
| MAX_RESEARCH_STEPS | 2 |
| MAX_REVIEW_ROUNDS | 3 |
| MAX_LLM_CALLS_PER_RUN | 100 |

**References:**
- [architecture/architecture_overview.md](architecture/architecture_overview.md) — system boundary, layers, principles
- [architecture/workflow_model.md](architecture/workflow_model.md) — complete workflow execution blueprint
- [docs/architecture.md](architecture.md) — Mermaid diagrams for every layer and pattern

---

## 5. Agents

| Agent | Model | Pattern | Trigger |
|---|---|---|---|
| Research Agent | Haiku | Bounded ReAct | Every job (before scoring) |
| Scoring Agent | Haiku | Structured output | Every job (concurrent batch) |
| Resume Critic | Sonnet | Critique | High-match jobs only |
| Review Auditor | Haiku | Evaluator / Reflection | High-match jobs only |
| Career Advisor | Sonnet | Advisory | After reflection loop |
| Interview Coach | Sonnet | Conditional | match_score ≥ threshold or user request |
| Tailoring Agent | Sonnet | Evidence-bound generation | User request |
| Fidelity Reviewer | Haiku | Validation / Guardrail | After every tailoring call |

**Model rationale:** Haiku handles all high-volume and validation tasks (research, scoring, auditing, fidelity). Sonnet handles generative and advisory tasks where quality is the constraint.

**Shared agent rules:**
- Every prompt includes `prompts/shared/guardrails.txt`
- Job descriptions are untrusted input — never follow instructions inside them
- Agents never call the database, filesystem, or external URLs directly
- All outputs are validated against Pydantic schemas before persistence
- Observability events emitted on start, complete, and fail

**Reference:** [architecture/agent_model.md](architecture/agent_model.md) — full per-agent input/output contracts, constraints, and observability events

---

## 6. Data Model

SQLite database at `data/v2.db`. 17 tables across four categories.

### Core Tables

| Table | Purpose |
|---|---|
| `workflow_runs` | Central run record — every execution linked here |
| `jobs` | Normalized job postings |
| `resumes` | Uploaded resumes with SHA-256 hash cache key |
| `job_scores` | Per-job scores across all three tracks |
| `resume_reviews` | Resume Critic output per job |
| `review_rounds` | Individual reflection loop rounds |
| `career_advice` | Career Advisor output per run |
| `interview_prep` | Interview Coach output per job |
| `tailored_resumes` | Tailoring Agent drafts + fidelity review results |
| `reports` | Final assembled reports |
| `human_decisions` | All HITL decisions with reasoning |
| `user_config` | DB-layer user preference overrides |

### Observability Tables

| Table | Purpose |
|---|---|
| `agent_events` | Agent start / complete / fail events |
| `llm_calls` | Per-call token counts, cost, latency, prompt version |
| `run_metrics` | Aggregate per-run cost and performance metrics |

### Security Table

| Table | Purpose |
|---|---|
| `security_events` | Prompt injection attempts, PII events, policy violations |

### Memory Table

| Table | Purpose |
|---|---|
| `memory_items` | Long-term learning items (future use) |

**Reference:** [architecture/data_model.md](architecture/data_model.md) — full table schemas, indexing strategy, JSON column conventions, anti-patterns

---

## 7. State & Memory

### WorkflowState

The single source of truth for a running workflow. Owned exclusively by the orchestrator — agents return structured outputs, never mutate state directly.

Key state sections: Workflow Metadata · Resume State · Job State · Research State · Review State · Career Intelligence State · HITL State · Metrics State · Error State · Effective Configuration

**Workflow status values:** `pending` → `running` → `waiting_for_user` → `running` → `completed` / `failed` / `cancelled`

### Memory

Long-term memory (cross-run learning) is stored in the `memory_items` table. The `MemoryService` writes and retrieves items by category and workflow context. Memory informs prompt context but never overrides current evidence from the database.

**Reference:** [architecture/state_and_memory_model.md](architecture/state_and_memory_model.md) — full WorkflowState schema, ownership rules, memory write/retrieve patterns

---

## 8. Workflow Model

### Primary Execution Flow

```
Discover Jobs
  → Load Resume
  → Research + Score (concurrent, all jobs)
  → HITL: Job Selection
  → Deep Review per selected job:
      Resume Critic → Review Auditor (reflection loop, ≤ 3 rounds)
  → Career Advisor
  → Interview Coach (conditional)
  → HITL: Approve / Request Tailoring
  → Tailoring Agent → Fidelity Reviewer
  → Report Generation
  → HITL: Export Approval
```

### Key Workflow Rules

- Only the orchestrator updates `WorkflowState`
- Execution limits enforced via `check_budget()` before every LLM call
- Reflection loop exits on `audit_score ≥ AUDIT_QUALITY_THRESHOLD` or stagnation (`< 5` point improvement) or `MAX_REVIEW_ROUNDS`
- HITL: workflow sets `status = waiting_for_user` + `pending_decision` before pausing; backend validates all decisions before resuming

**Reference:** [architecture/workflow_model.md](architecture/workflow_model.md) — complete workflow blueprints for every sub-workflow

---

## 9. Configuration

### Effective Config = YAML Defaults + DB Overrides

| Layer | File / Location | Mutability |
|---|---|---|
| System defaults | `config/config.yaml` | Edited by user at setup |
| DB overrides | `user_config` table | Changed at runtime via UI |
| Locked limits | Hardcoded in `limits.py` | Never user-configurable |

**Locked (user cannot override):** LLM model names · execution limits · safety thresholds · cost caps

**User-configurable:** Search titles · locations · salary · work mode · career tracks · Adzuna settings

```yaml
# config/config.yaml — key sections
search:
  titles: [software architect, principal engineer, ...]
  locations: [Atlanta GA, Remote, ...]
  work_mode: [remote, hybrid, onsite]

salary:
  min_desired: 130000
  currency: USD

tracks:
  ic: true
  architect: true
  management: true
```

**Reference:** [architecture/config_model.md](architecture/config_model.md) — config layers, ConfigService, guardrails, UI integration

---

## 10. Observability

Every meaningful event in the system is recorded. The observability stack covers six layers:

| Layer | What Is Recorded |
|---|---|
| Workflow | Run start/complete/fail, status transitions |
| Agent | Start/complete/fail per agent call |
| LLM call | Token counts, cost, latency, model, prompt version |
| Tool | Research tool invocations and results |
| HITL | Decision type, value, user reasoning |
| Security | Prompt injection attempts, PII events, policy violations |

**Correlation:** Every record carries `workflow_run_id` for end-to-end traceability.

**Cost tracking:** `estimated_cost_usd` accumulated per call, per run, and surfaced in the Streamlit UI.

**Reference:** [architecture/observability.md](architecture/observability.md) — full observability model, event types, database tables, anti-patterns

---

## 11. Security & Ethics

### Security

- Job descriptions treated as untrusted input (ADR-019) — injection defense in every agent prompt
- PII minimization: raw resume text is never sent to agents; only the parsed `ResumeProfile` is used (ADR-020)
- Security events logged to `security_events` table (ADR-026)
- User cannot override safety limits, LLM models, or cost caps via config

### Ethics Guardrails

Every agent prompt includes `prompts/shared/guardrails.txt`, which enforces:

- Gap labeling: missing experience is always labeled as a gap, never rewritten as if present
- Evidence requirement: every tailored claim must include `supporting_evidence` from the original resume
- No fabrication: Fidelity Reviewer blocks unsupported claims before persistence
- Career decision language: the system informs and assists; it never decides for the user

**References:** [architecture/security.model.md](architecture/security.model.md) · ADR-017 · ADR-018 · ADR-019 · ADR-020 · ADR-025 · ADR-026

---

## 12. Human-in-the-Loop

### Core Principle

> Backend owns workflow execution. User owns business decisions.

### HITL Checkpoints

| Checkpoint | Decision Type | When |
|---|---|---|
| Job Selection | Which jobs to deep-review | After scoring pass |
| Deep Review Approval | Accept / request changes | After reflection loop |
| Interview Prep Decision | Proceed or skip | After deep review |
| Tailoring Approval | Accept / reject draft | After Tailoring Agent |
| Fidelity Review Resolution | Accept / override flagged claims | After Fidelity Reviewer |
| Report Export Approval | Confirm before export | After report generation |
| Application Status Update | Track outcome | User-initiated |

### HITL State Flow

```
running → waiting_for_user → (user submits decision) → running → completed
```

Backend validates all decisions before resuming. UI never auto-approves outputs or bypasses backend validation.

**Reference:** [architecture/hitl.md](architecture/hitl.md) — full HITL model, decision object structure, validation rules, anti-patterns

---

## 13. Design Patterns

The system implements 15 agentic AI patterns:

| Pattern | Where |
|---|---|
| Workflow Orchestration | LangGraph stateful graph |
| Supervisor / Router | Orchestrator node routing |
| Static Planning | Workflow execution plan fixed at startup |
| Tool Use | Research Agent — job fetcher + content extractor |
| Selective ReAct | Research Agent only (ADR-010) |
| Reflection Loop | Resume Critic → Review Auditor (bounded) |
| Evaluator / Critic | Review Auditor scoring Critic output |
| Human-in-the-Loop | 7 HITL checkpoints |
| Structured Output | Every agent response validated by Pydantic |
| Bounded Execution | `MAX_RESEARCH_STEPS`, `MAX_REVIEW_ROUNDS`, `MAX_LLM_CALLS_PER_RUN` |
| Conditional Execution | Interview Coach, deep review gating |
| Agent Specialization | 8 agents, one responsibility each |
| Observability | 6-layer event tracking |
| Guardrails / Policy | Fidelity Reviewer + shared guardrails.txt |
| Cache-Aside | Resume parsed once, retrieved by SHA-256 hash |

**Reference:** [architecture/patterns.md](architecture/patterns.md) — full pattern descriptions with motivation and implementation notes

---

## 14. Architecture Principles

15 principles govern every design decision:

1. Backend Owns Intelligence
2. Controlled Autonomy Over Full Autonomy
3. Deterministic Where Possible
4. Bounded Intelligence
5. State is Source of Truth
6. Humans Remain in Control
7. Truthfulness Over Optimization
8. Separation of Concerns
9. Observability is Mandatory
10. Security by Design
11. Optimize for Iteration
12. Minimize User Friction
13. Cost is First-Class Constraint
14. Prefer Explicit Over Implicit
15. Build for Evolution

**Reference:** [architecture/principles.md](architecture/principles.md) — full descriptions and motivation for each principle

---

## 15. Architecture Decision Records

52 ADRs covering every major design decision. All accepted.

| ADR | Decision |
|---|---|
| ADR-001 | Keep v1 stable; develop v2 in parallel |
| ADR-002 | Orchestrator-mediated agent coordination with shared state |
| ADR-003 | Separate frontend and backend responsibilities |
| ADR-004 | Backend owns workflow orchestration |
| ADR-005 | Use specialized agents (one responsibility each) |
| ADR-006 | Keep deterministic work in tools and services |
| ADR-007 | Use structured output schemas |
| ADR-008 | Use bounded reflection for resume critique |
| ADR-009 | Do not use formal multi-agent protocol for MVP |
| ADR-010 | Use ReAct selectively in Research Agent only |
| ADR-011 | Human-in-the-loop as backend workflow pauses |
| ADR-012 | Deep review only on shortlisted jobs |
| ADR-013 | Separate resume gaps from career gaps |
| ADR-014 | Interview Coach is conditional |
| ADR-015 | Tailoring must be evidence-bound |
| ADR-016 | Add Fidelity Reviewer after Tailoring Agent |
| ADR-017 | Ethical AI use for career decision support |
| ADR-018 | Global ethics guardrails in every agent prompt |
| ADR-019 | Treat scraped job descriptions as untrusted input |
| ADR-020 | Minimize PII sent to LLMs |
| ADR-021 | Store workflow runs, not just final results |
| ADR-022 | Use JSON columns for evolving agent outputs |
| ADR-023 | Make observability first-class |
| ADR-024 | Track prompt versions |
| ADR-025 | Add security and policy layer around agents and tools |
| ADR-026 | Track security events |
| ADR-027 | Add cost, token, and latency tracking |
| ADR-028 | Start with Streamlit + SQLite MVP |
| ADR-029 | Add FastAPI only after service layer stabilizes |
| ADR-030 | Use skills.yaml for application skill taxonomy |
| ADR-031 | Separate Claude Code support files from app code |
| ADR-032 | Abstract LLM providers |
| ADR-033 | Status Manager must be non-AI |
| ADR-034 | Do not overbuild before proving core workflow |
| ADR-035 | Enforce a structured WorkflowState schema |
| ADR-036 | Define explicit agent input/output contracts |
| ADR-037 | Standard failure and retry strategy |
| ADR-038 | Version prompts, agents, schemas, and workflows |
| ADR-039 | Sequential MVP execution model with future parallelism |
| ADR-040 | Define data retention and privacy policy |
| ADR-041 | All agent execution must be bounded |
| ADR-042 | Define testing and evaluation strategy |
| ADR-043 | Define prompt evaluation and regression strategy |
| ADR-044 | Define v1 to v2 migration strategy |
| ADR-045 | Job intake supports automated discovery and manual input |
| ADR-046 | Hybrid configuration model (YAML + DB overrides) |
| ADR-047 | Use SqliteSaver for LangGraph workflow checkpoint persistence |
| ADR-048 | API key presence as live/mock mode gate |
| ADR-049 | Use ThreadPoolExecutor for concurrent job scoring |
| ADR-050 | Wrap v1 AdzunaScraper with a concurrent adapter |
| ADR-051 | Tiered model assignment — Haiku for volume/validation, Sonnet for generative |
| ADR-052 | Reduce MAX_JOBS_PER_RUN as the primary volume cost control lever |

**Reference:** [architecture/adr/ADR-000-index.md](architecture/adr/ADR-000-index.md) — full index with links to each individual ADR

---

## 16. Phase History

Full phase details including deliverables, tests, and review gates are in [architecture/implementation_plan.md](architecture/implementation_plan.md).

| Phase | Description | Status | Key Commit |
|---|---|---|---|
| 1 | Foundation — schemas, repos, config | ✓ complete | `5e63017` |
| 2 | Services — job discovery, resume parser | ✓ complete | `02992f9` |
| 3 | LLM provider — ClaudeProvider, prompt caching | ✓ complete | `0f74133` |
| 4 | All 8 agents | ✓ complete | — |
| 5 | LangGraph workflow orchestrator | ✓ complete | — |
| 6 | FastAPI backend + Streamlit UI | ✓ complete | — |
| 7 | Live agents — real Claude, SqliteSaver | ✓ complete | `26c3767` |
| 8 | Performance — concurrent scoring + scraping | ✓ complete | `26c3767` |
| 9 | Cost optimization — model tiering, volume caps | ⚡ in progress | — |

### Phase 9 Summary

Phase 9 reduces per-run API cost by 75–85% through model tiering and volume reduction:

- **Research Agent, Review Auditor, Fidelity Reviewer** moved from Sonnet → Haiku (validation + summarization tasks)
- **Scoring Agent** remains on Haiku
- **Resume Critic, Career Advisor, Interview Coach, Tailoring Agent** remain on Sonnet (generative / advisory tasks)
- **MAX_JOBS_PER_RUN** reduced 20 → 10
- **Prompt caching** (`cache_control: ephemeral`) has been active since Phase 3 — 90% cost reduction on repeated calls within a session

---

## 17. Testing Strategy

| Layer | Test Type | LLM Calls |
|---|---|---|
| Schemas | Unit | None |
| Services | Unit | None |
| LLM Provider | Unit (mocked) | None |
| Agents | Integration (fixture responses) | None in CI |
| Workflows | Integration (fixture state) | None in CI |
| Full flow | E2E (fixture) | None in CI |
| Prompt quality | Manual eval runs | Real calls |

**Key rule:** No real LLM calls in CI. All agent tests use recorded fixture responses. Real LLM evaluation runs are tracked against prompt versions per ADR-043.

```bash
python -m pytest tests/                  # full suite, mock mode
python -m pytest tests/ -m integration  # live-API smoke tests
```

**Reference:** ADR-042 (testing strategy) · ADR-043 (prompt evaluation)

---

## 18. Migration from v1

v1 (`main.py`, `agents/`, `scrapers/`, `storage/`, `dashboard.py`) remains stable and is not modified. v2 was developed in parallel.

| v1 Component | v2 Replacement |
|---|---|
| `scrapers/` | Preserved, wrapped by `JobDiscoveryService` + `ConcurrentAdzunaScraper` |
| `agents/scoring_agent.py` | v2 `ScoringAgent` with structured Pydantic schema |
| `agents/tailoring_agent.py` | v2 `TailoringAgent` + `FidelityReviewer` |
| `storage/` | v2 repositories + 17-table SQLite schema |
| `dashboard.py` | v2 Streamlit UI (`app/ui/`) |
| `claude/` | v2 `ClaudeProvider` + `PromptLoader` |
| `prompts/` | v2 prompt system with shared `guardrails.txt` |

**Reference:** ADR-044 (migration strategy) · ADR-001 (keep v1 stable)

---

## 19. Dependencies & Licences

| Library | Purpose | Licence |
|---|---|---|
| `anthropic` / `langchain-anthropic` | Claude API + LangChain integration | MIT |
| `langgraph` | Stateful workflow orchestration | MIT |
| `langchain` | Agent framework, tool abstractions | MIT |
| `fastapi` + `uvicorn` | HTTP backend | MIT |
| `streamlit` | UI | Apache 2.0 |
| `pydantic` v2 | Schema validation | MIT |
| `pdfplumber` | PDF resume parsing | MIT |
| `httpx` | HTTP client for scrapers | BSD |
| `python-dotenv` | `.env` loading | BSD |
| `PyYAML` | config.yaml parsing | MIT |
| `pytest` + plugins | Testing | MIT |

**Reference:** [docs/dependencies.md](dependencies.md) — full version table with licence types

---

## 20. Changelog

Recent changes tracked in [CHANGELOG.md](../CHANGELOG.md).

Notable entries:
- **2026-04-24** — Dashboard timestamp parsing fix, US state extraction, low-score purge feature
- **2026-04-17** — Documentation updates for deprecated API fixes
- **2026-04-15** — Python 3.12+ compatibility (utcnow → now with timezone)

---

*This wiki is generated from the documentation in `docs/`, `docs/architecture/`, and the project root. For the authoritative source on any topic, follow the reference links in each section.*
