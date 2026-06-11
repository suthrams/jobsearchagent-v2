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

* Critical, irreversible steps require user approval (see Principle 19)
* The workflow itself does not pause — the in-graph `interrupt()` was retired in ADR-059; human decisions happen out-of-graph, on demand, validated server-side
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

# 16. Fix the Product, Not the Profile

A defect or request surfaced by ONE user is a sample, not the scope.

* Per-profile **configuration** is legitimately specific (`effective_config`, ADR-062)
* Per-profile **logic, heuristics, and fixes are not** — never hardcode for the reporting profile
* Extract the general class, fit it to the whole app, and state the boundary in the bug/ADR

*Enforced by:* CLAUDE.md workflow rule; the BUG-010 / BUG-011 RCAs + their forcing-function tests.

---

# 17. Profile-Specificity Lives in Data, Not Shared Assets

Shared assets (prompts, code, config defaults) stay field-agnostic.

* A shared prompt **derives** its per-user behavior from the per-profile context it already receives (`resume_profile`, `target_roles`, `seniority_signals`) — it never hardcodes one profile's domain
* This is the sharper instance of Principle 16 applied to LLM assets, and it is what lets one shared prompt serve a large, heterogeneous user base

*Enforced by:* the relevance-filter prompt v3 + its field-agnostic forcing test (ADR-079); the two-layer `effective_config` (ADR-062).

---

# 18. Filter-Input, Not Outcome-Tracking

The system accepts signals the user gives it; it never records outcomes about the user.

* In scope: filters, exclusions, favorites, review-later — inputs the user → system
* Out of scope: Apply / Save-status / applied / stage / outcome fields — the career decision stays human-owned

*Enforced by:* the no-application-tracking rule; the favorites / review-later no-status schema forcing test (ADR-090, ADR-100).

---

# 19. Gate the Irreversible, Not Everything

HITL is reserved for genuinely irreversible actions.

* Repeatable, post-hoc operations run **out-of-graph** with no `interrupt()`, so the workflow never blocks
* The human owns the decision (refines Principle 6), but the graph does not pause — decisions are validated server-side, on demand

*Enforced by:* ADR-055 / ADR-059 / ADR-061 (the in-graph interrupt path was retired in ADR-059).

---

# 20. Prove Load-Bearing Promises at the Seam

Module-mock tests are not enough for cross-cutting invariants.

* Every load-bearing promise (PII redaction, model pins, no-status, idempotency, the directory-as-source bug index) gets a forcing-function / invariant test that spans the seam and fails the build on drift
* Validate every LLM output against a Pydantic schema before it is persisted

*Enforced by:* the PII source-scan test (ADR-069), the model-pin test (ADR-058), the no-status tests (ADR-090/100), and the schema-validation contract.

---

# Final Principle

This system is not an autonomous AI.

It is:

A controlled reasoning system that helps users make better decisions.
