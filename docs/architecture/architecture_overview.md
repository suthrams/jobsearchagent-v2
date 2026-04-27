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
Shortlist + Human Decision (HITL)
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
* implements HITL pauses

---

### 6.3 Agent Layer

Agents perform reasoning tasks only.

Core agents:

* Scoring Agent
* Research Agent (bounded ReAct)
* Resume Critic
* Review Auditor
* Career Advisor
* Interview Coach
* Tailoring Agent
* Fidelity Reviewer

Agents do not execute actions directly.

---

### 6.4 Tools and Services Layer

Deterministic components:

* job discovery and scraping
* job normalization
* resume parsing
* skill normalization
* report generation
* status management
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

The system stores workflows, not just results.

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

### Tailoring

Tailor → Validate → Approve

### Interview Prep

Generate role-specific preparation

### Human-in-the-Loop

Pause → Decision → Resume

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

Memory is used selectively.

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

The system follows:

```text
Score many → Deep review few
```

Guardrails:

* bounded research steps
* bounded review loops
* bounded LLM usage
* bounded cost per run

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


