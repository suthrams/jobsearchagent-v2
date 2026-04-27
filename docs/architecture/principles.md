# Architecture Principles – jobsearchagent-v2

## Overview

This document defines the core principles that guide all architectural decisions in jobsearchagent-v2.

While ADRs capture specific decisions and patterns describe implementation approaches, principles define how to think when extending or modifying the system.

These principles are non-negotiable and should be used as the default decision framework.

---

# 1. Backend Owns Intelligence

The backend workflow system is the source of truth for execution.

* The UI must not orchestrate agents
* The UI is only a control surface
* All workflows, routing, and decisions belong in the backend

---

# 2. Controlled Autonomy Over Full Autonomy

Agents are allowed to reason, but not act independently.

* Agents do not execute actions directly
* Agents request actions through tools
* All execution is controlled by the orchestrator

---

# 3. Deterministic Where Possible, Intelligent Where Necessary

Use deterministic logic whenever possible.

* Parsing, fetching, storage, and status updates must be deterministic
* LLMs should only be used for reasoning tasks

---

# 4. Bounded Intelligence

All reasoning must have limits.

* No infinite loops
* No unbounded tool use
* No uncontrolled cost growth

Every loop, agent, and workflow must have explicit stopping conditions.

---

# 5. State is the Single Source of Truth

All decisions must be based on structured workflow state.

* Agents read from state
* Agents write structured outputs to state
* No hidden or implicit context

---

# 6. Humans Remain in Control

The system supports decisions but does not make them.

* Critical steps require user approval
* The system must pause for user input when needed
* Avoid authoritative or deterministic language

---

# 7. Truthfulness Over Optimization

The system must never misrepresent the user.

* No fabricated experience
* No invented metrics
* No exaggeration of scope

If something is missing, it must be labeled as a gap.

---

# 8. Separation of Concerns

Keep system responsibilities clearly divided.

* Agents → reasoning
* Tools → execution
* Services → deterministic logic
* Orchestrator → control flow

---

# 9. Observability is Mandatory

Every meaningful action must be traceable.

* Track workflow runs
* Track agent decisions
* Track LLM calls
* Track cost and performance

If something cannot be observed, it cannot be trusted.

---

# 10. Security by Design

Assume all external input is untrusted.

* Job descriptions are untrusted input
* Do not allow prompt injection
* Restrict tool access
* Validate all inputs

---

# 11. Optimize for Iteration, Not Perfection

The system should evolve through iteration.

* Start simple
* Prove value
* Expand gradually

Avoid building full systems before validating core workflows.

---

# 12. Minimize User Friction

The system should reduce effort for the user.

* Automated job discovery is primary
* Resume upload is optional
* Manual inputs are fallback, not required

---

# 13. Cost is a First-Class Constraint

LLM usage must be controlled and measurable.

* Limit expensive operations
* Track token usage
* Avoid unnecessary deep workflows

---

# 14. Prefer Explicit Over Implicit

Make system behavior visible and predictable.

* Explicit schemas
* Explicit workflows
* Explicit decisions

Avoid hidden logic or implicit assumptions.

---

# 15. Build for Evolution

The architecture should support future growth.

* Provider abstraction for LLMs
* Modular agent design
* Replaceable UI layer
* Extensible workflow engine

---

# Final Principle

This system is not an autonomous AI.

It is:

A controlled reasoning system that helps users make better decisions.
