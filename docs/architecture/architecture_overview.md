 # Architecture Overview – jobsearchagent-v2

---

## 1. Purpose

JobSearchAgent v2 is a **multi-agent career intelligence system** that helps users:

* discover relevant jobs automatically
* evaluate job fit
* identify resume gaps vs career gaps
* improve positioning
* prepare for interviews
* tailor resumes without fabricating experience
* track decisions, reasoning, and outcomes

The system is not just a job scraper or resume analyzer.

It is:

> A controlled reasoning system for career decision support.

---

## 2. System Boundary

The system integrates:

* automated job discovery (primary intake)
* optional manual job input
* optional resume upload
* stored resume profiles
* structured evaluation workflows
* agent-based reasoning
* deterministic services
* SQLite-based persistence
* full observability and traceability

Infrastructure scaling (e.g., Postgres, distributed execution) is intentionally deferred.

---

## 3. High-Level Architecture

```text
Frontend UI (Streamlit)
        ↓
Workflow Orchestrator (Backend)
        ↓
Job Discovery + Resume Profile Services
        ↓
Scoring Layer
        ↓
Auto-select qualifying jobs (no in-graph pause; ADR-059)
        ↓
Deep Review Workflow
        ↓
Agents (Research, Critic, Auditor, Advisor, etc.)
        ↓
Tools / Services (deterministic execution)
        ↓
SQLite (state + history + observability)
```

---

## 4. Core Design Principles

The architecture is guided by:

* Backend owns intelligence and orchestration
* UI is a thin control surface
* Controlled autonomy over full autonomy
* Deterministic where possible, intelligent where necessary
* Bounded execution everywhere
* State is the source of truth
* Humans remain in control of decisions
* Truthfulness over optimization
* Observability is mandatory
* Security and ethics are enforced by design

Details are defined in:

```text
architecture_principles.md
patterns.md
docs/adr/
```

---

## 5. Input Model

### Job Intake

Primary:

* automated job discovery (scraper/API)

Optional:

* manual job URL
* pasted job description

### Resume Intake

Default:

* stored resume profile

Optional:

* upload new resume
* select previous version

The system minimizes user friction by making manual inputs optional.

---

## 6. System Layers

### 6.1 UI Layer

* collects inputs
* displays results
* handles user decisions
* does not orchestrate workflows

---

### 6.2 Workflow / Orchestration Layer

* controls execution flow
* manages workflow state
* invokes agents, tools, and services
* handles loops and stopping conditions
* runs end to end with no in-graph pause; human decisions are out-of-graph (ADR-059)

---

### 6.3 Agent Layer

Agents perform reasoning tasks only.

Core agents:

* Relevance Filter (opt-in pre-scoring triage, ADR-079)
* Research Agent (bounded ReAct)
* Scoring Agent
* Resume Critic
* Review Auditor
* Career Advisor
* Interview Coach
* Tailoring Agent
* Fidelity Reviewer
* Resume Reviewer (Resume Clinic, ADR-066)

Agents do not execute actions directly.

---

### 6.4 Tools and Services Layer

Deterministic components:

* job discovery and scraping
* job normalization
* resume parsing
* PII redaction at the LLM seam (context trimmer, ADR-069)
* deep-review / Resume Clinic runners and the resume text renderer
* report generation
* observability logging

Rule:

> Agents reason. Tools and services execute.

---

### 6.5 Provider Layer

Abstracts LLM providers:

* Claude
* OpenAI (optional)

Agents depend on a unified interface rather than a specific provider.

---

### 6.6 Persistence Layer

SQLite stores:

* workflow runs
* jobs and resumes
* scores and reviews
* agent events and LLM calls
* human decisions
* metrics and reports
* memory items
* profiles (`users`, ADR-062)

The system stores workflows, not just results.

**Multi-user (ADR-062).** The app serves multiple profiles from one install under
sequential use, each with its own resume, config, memory, cost view, and history.
Identity is resolved by a single seam — backend `get_current_user_id` (a
`?user_id=` query parameter, default `"0"`) and the UI's mirror — so adding real
authentication later changes one function, not the data model. Isolation is
cooperative, not enforced (see §11 / `security.model.md` §4.1).

---

## 7. Core Workflows

### Job Discovery

Search → Scrape → Normalize → Store

### Scoring

Resume + Jobs → Score → Rank

### Deep Review

Research → Critic → Auditor → Career Advice

### Reflection Loop

Critic ↔ Auditor (bounded iterations)

### Tailoring (out-of-graph, on demand)

Tailor → Validate (Fidelity Reviewer) → Decide (approve / revise / reject / edit)

### Interview Prep

Generate role-specific preparation

### Human-in-the-Loop (out-of-graph)

Run completes → user triggers an on-demand op → decision recorded (no graph pause; ADR-059)

---

## 8. Agentic Pattern Strategy

The system uses patterns selectively:

* Workflow orchestration (core control)
* Static planning (predefined flow)
* Tool use (execution layer)
* ReAct (research only)
* Reflection (critique loop)
* Evaluator/critic (audit)
* Human-in-the-loop (decisions)
* Structured outputs (reliability)
* Bounded execution (safety)

The system avoids:

* global ReAct
* fully autonomous agents
* unbounded planning

---

## 9. State and Memory

### Workflow State

Short-term execution context:

* resume profile
* job data
* scores
* review rounds
* decisions

### Memory

Long-term learning:

* user preferences
* job patterns
* successful outcomes

State is authoritative for execution.

> **Designed, not yet wired into the runtime.** The `memory_items` table,
> `MemoryRepository`, and per-profile scoping exist, but no agent or node reads or
> writes memory today (there is no `MemoryService` / `app/memory/`). When wired, it
> is **isolated per profile** (ADR-062) — one person's learned patterns never seed
> another's runs. See `state_and_memory_model.md` and `CLAUDE.md`.

---

## 10. Observability Overview

The system tracks:

* workflow lifecycle
* agent execution
* LLM calls (tokens, cost, latency)
* reflection rounds
* human decisions
* errors and retries
* security events
* API requests (method/route/status/latency) and live health: `GET /health` +
  `GET /readyz` probe the shared dependencies (ADR-084)

Observability is required for debugging, cost control, and trust.

---

## 11. Security and Ethics Overview

Security controls:

* PII minimization
* no raw resume logging
* prompt injection defense
* tool allowlists
* schema validation

Ethical constraints:

* no fabricated experience
* no invented metrics
* clear distinction between resume gaps and career gaps
* user remains in control

---

## 12. Performance Strategy

The system is a **funnel** that narrows from many cheap jobs to a few expensive
ones, with the width owner-controlled inside hard ceilings (ADR-061):

```text
Discover many (<=50) -> score the worthwhile (<=25) -> deep-review the few (3)
```

Guardrails:

* configurable scored width (`scoring.max_scored`, ceiling 25) and discovery net
  (`search.max_discovered`, ceiling 50) — system-wide default + per-run override
* optional reasoning pre-filter between discover and score (`search.relevance_filter`,
  ADR-079): one cheap LLM pass drops seniority/relevance mismatches before scoring
* optional posting-age cap at discovery (`search.max_posting_age_days`, ADR-080):
  deterministic drop of stale postings (dead-apply-link proxy), upstream of both
  the relevance filter and scoring
* bounded research steps and review loops
* bounded auto-selection (`MAX_SELECTED_JOBS` = 3 reach in-graph deep review)
* `MAX_LLM_CALLS_PER_RUN` = 200 as the absolute per-run cost backstop

Beyond the in-graph funnel, the human can pull **any scored job** through
out-of-graph on-demand operations (deep review, tailoring, interview prep;
ADR-055/061) — the narrow end is owner-driven, not limited to the auto-selected
few.

SQLite is sufficient for current scale due to bounded execution.

---

## 13. Architecture Summary

JobSearchAgent v2 is a full-featured, workflow-driven system that combines:

* structured orchestration
* specialized agents
* deterministic services
* bounded reasoning
* human oversight
* persistent state and observability

The defining idea is:

> Keep agents specialized, keep state structured, keep loops bounded, and keep humans in control.


