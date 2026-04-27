# Agentic AI Patterns – jobsearchagent-v2

## Overview

This document defines the core agentic AI patterns used in **jobsearchagent-v2**.
These patterns guide how agents, workflows, and services interact to ensure the system remains:

* Predictable
* Testable
* Observable
* Secure
* Ethically grounded

The system is intentionally designed as a **controlled reasoning system**, not an autonomous agent system.

---

## Pattern Summary

| Pattern                | Type         | Scope                 | Purpose                        |
| ---------------------- | ------------ | --------------------- | ------------------------------ |
| Workflow Orchestration | Core         | System                | Controls execution flow        |
| Supervisor / Router    | Core         | System                | Decides next step              |
| Static Planning        | Core         | System                | Defines execution sequence     |
| Tool Use               | Core         | Agent                 | Executes deterministic actions |
| Selective ReAct        | Adaptive     | Agent (Research only) | Enables bounded reasoning      |
| Reflection Loop        | Core         | Critique              | Improves output quality        |
| Evaluator / Critic     | Core         | Quality               | Scores and validates outputs   |
| Human-in-the-Loop      | Control      | System                | Ensures user control           |
| Structured Output      | Core         | All agents            | Ensures reliable outputs       |
| Bounded Execution      | Safety       | System                | Prevents runaway behavior      |
| Conditional Execution  | Optimization | Workflow              | Controls cost and relevance    |
| Agent Specialization   | Design       | System                | Improves clarity and quality   |
| Observability          | System       | System                | Enables debugging and tracking |
| Guardrails / Policy    | Safety       | System                | Enforces ethics and security   |
| Memory (Future)        | Learning     | System                | Enables adaptation over time   |

---

## 1. Workflow Orchestration

**Definition**
A centralized backend workflow controls execution across all agents and services.

**Where Used**

* `app/workflows/`
* Future LangGraph implementation

**Why It Matters**

* Ensures deterministic execution
* Prevents uncontrolled agent behavior
* Enables debugging and observability

---

## 2. Supervisor / Router Pattern

**Definition**
A control layer that determines the next step based on workflow state.

**Where Used**

* Workflow orchestrator
* Conditional branching logic

**Why It Matters**

* Centralizes decision-making
* Keeps agents simple and focused
* Enables Human-in-the-Loop integration

---

## 3. Static Planning (Macro Planning)

**Definition**
A predefined workflow plan encoded in code rather than generated dynamically by an LLM.

**Example Flow**

```
Parse → Fetch → Research → Score → Critic → Audit → Advisor → Report
```

**Why It Matters**

* Predictable execution
* Easier debugging and testing
* Strong alignment with architectural decisions

---

## 4. Tool Use Pattern

**Definition**
Agents request tools to perform deterministic actions instead of executing them directly.

**Where Used**

* Resume parsing
* Job fetching / scraping
* Data normalization
* Report generation

**Why It Matters**

* Separates reasoning from execution
* Improves security and control
* Prevents hallucinated actions

---

## 5. Selective ReAct Pattern

**Definition**
A bounded reasoning loop:

```
Thought → Action → Observation → Repeat (bounded)
```

**Where Used**

* Research Agent ONLY

**Why It Matters**

* Enables flexible research
* Avoids global system complexity
* Keeps reasoning controlled

---

## 6. Reflection Loop Pattern

**Definition**
An iterative improvement loop:

```
Resume Critic → Review Auditor → Repeat
```

**Where Used**

* Resume critique workflow

**Why It Matters**

* Improves output quality
* Introduces self-correction
* Differentiates system from basic LLM outputs

---

## 7. Evaluator / Critic Pattern

**Definition**
A secondary agent evaluates the output of another agent.

**Where Used**

* Review Auditor

**Why It Matters**

* Enables scoring (e.g., 1–100)
* Detects weak or inconsistent outputs
* Drives reflection loop

---

## 8. Human-in-the-Loop (HITL)

**Definition**
User intervention points embedded within workflows.

**Pattern**

```
Pause → Request Input → Resume
```

**Where Used**

* Job selection for deep review
* Resume tailoring approval
* Interview preparation

**Why It Matters**

* Maintains user control
* Prevents incorrect automation
* Builds trust in the system

---

## 9. Structured Output Pattern

**Definition**
All agent outputs conform to strict schemas (e.g., Pydantic models or JSON contracts).

**Where Used**

* Job scoring
* Resume reviews
* Audit results
* Career advice

**Why It Matters**

* Enables validation
* Supports persistence
* Improves UI rendering and testing

---

## 10. Bounded Execution Pattern

**Definition**
Explicit limits on loops and operations.

**Examples**

```
MAX_REVIEW_ROUNDS = 3
MAX_RESEARCH_STEPS = 2
MAX_LLM_CALLS
```

**Why It Matters**

* Prevents runaway costs
* Ensures predictable runtime
* Improves system safety

---

## 11. Conditional Execution Pattern

**Definition**
Only execute certain workflows when conditions are met.

**Examples**

* Deep review only for shortlisted jobs
* Interview Coach for high match scores

**Why It Matters**

* Reduces cost
* Improves performance
* Focuses system effort on high-value cases

---

## 12. Agent Specialization Pattern

**Definition**
Each agent has a single, well-defined responsibility.

**Agents**

* Scoring Agent
* Research Agent
* Resume Critic
* Review Auditor
* Career Advisor
* Interview Coach
* Tailoring Agent

**Why It Matters**

* Improves prompt clarity
* Simplifies debugging
* Enables targeted improvements

---

## 13. Observability Pattern

**Definition**
Comprehensive tracking of system execution.

**Tracked Data**

* Workflow runs
* Agent events
* LLM calls
* Tokens, cost, latency
* Reflection rounds
* Human decisions

**Why It Matters**

* Enables debugging
* Supports performance tuning
* Provides cost visibility

---

## 14. Guardrails / Policy Pattern

**Definition**
System-level enforcement of safety, ethics, and security.

**Includes**

* Ethics guardrails in prompts
* Prompt injection defense
* PII minimization
* Tool access restrictions

**Why It Matters**

* Prevents hallucinations
* Ensures safe outputs
* Maintains trust

---

## 15. Memory Pattern (Future)

**Definition**
Long-term learning across workflow runs.

**Examples**

* User preferences
* Rejected suggestions
* Successful patterns

**Why It Matters**

* Enables personalization
* Improves recommendations
* Supports adaptive intelligence

---

## Pattern Strategy

### What We Avoided

* Global ReAct (too complex and unpredictable)
* Fully autonomous agents (reduces control and trust)
* Dynamic planning agents (premature for v2)

---

### What We Enforced

* Controlled autonomy
* Deterministic orchestration
* Bounded reasoning
* Strong guardrails

---

## Key Insight

The system is not:

```
A collection of autonomous agents
```

It is:

```
A controlled reasoning system
with structured workflows and bounded intelligence
```

---

## Future Evolution

Potential future patterns:

* Planning Agent (controlled introduction)
* Adaptive workflow routing
* Parallel execution
* Memory-driven personalization

These should only be introduced after:

* Observability is mature
* Evaluation framework is established
* Core workflows are stable
