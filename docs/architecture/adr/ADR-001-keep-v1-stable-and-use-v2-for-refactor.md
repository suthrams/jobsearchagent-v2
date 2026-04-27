# ADR-001: Keep v1 Stable and Use v2 for Refactor

## Status
Accepted (Revised)

## Context
The existing jobsearchagent v1 already contains working functionality for resume parsing, job scraping, scoring, persistence, dashboarding, and tailoring. v2 will introduce deeper architectural changes, including specialized agents, structured outputs, reflection loops, observability, ethics, and LangGraph orchestration.

v2 will use LangChain and LangGraph as the orchestration and agent framework, alongside the direct Anthropic SDK. This supersedes the earlier MVP constraint of "direct Anthropic SDK only".

## Decision
Keep v1 stable and use jobsearchagent-v2 as the refactor and learning branch.

v2 will use:
- LangChain — agent tooling, prompt management, and chain composition
- LangGraph — workflow orchestration and stateful multi-step execution
- Direct Anthropic SDK — LLM provider calls via LangChain's Anthropic integration
- FastAPI — backend API layer (see ADR-029)

## Rationale
LangChain and LangGraph provide production-grade primitives for agent orchestration, state management, tool use, and reflection loops that align directly with v2's architecture. Using them avoids reinventing the orchestration layer and accelerates the implementation of patterns already defined in the architecture docs.

This avoids breaking the working v1 baseline while allowing v2 to evolve safely with a richer framework.

## Consequences

### Positive
- v1 remains usable
- v2 can evolve safely with proven orchestration primitives
- LangGraph handles workflow state, branching, and HITL natively
- LangChain provides tool use, prompt templating, and provider abstraction
- Migration can happen feature by feature

### Tradeoffs
- Temporary duplication between v1 and v2
- LangChain/LangGraph adds framework dependency
- Team must understand LangGraph state graph concepts

## Implementation Notes
- Do not make major architectural changes in v1
- Use LangGraph for workflow orchestration in `app/workflows/`
- Use LangChain for agent tool use, prompt templates, and chain composition
- Use direct Anthropic SDK via LangChain's `ChatAnthropic` integration
- Reference v1 behavior when migrating features
- SQLAlchemy, Celery, and Redis are explicitly excluded — not needed for MVP scope
