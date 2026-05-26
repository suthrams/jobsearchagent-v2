# Job Search Agent v2 — Documentation Index

> **Start at [wiki.md](wiki.md)** for the full system overview with links to every section.
> This index maps topic areas to their authoritative detail files.

---

## v2 Documentation

### Getting Started
| Document | Purpose |
|---|---|
| [../README.md](../README.md) | Project overview, quick start, architecture diagram, agent table |
| [user_guide.md](user_guide.md) | End-to-end v2 walkthrough — setup, running the backend, using the UI, HITL workflow |
| [features.md](features.md) | Complete v2 feature and capability reference |
| [wiki.md](wiki.md) | Wiki landing page — all 20 topic areas with pointers to detail files |

### Architecture
| Document | Purpose |
|---|---|
| [architecture/architecture_overview.md](architecture/architecture_overview.md) | System boundary, 7 layers, core principles |
| [architecture/agent_model.md](architecture/agent_model.md) | Per-agent input/output contracts, patterns, constraints, observability |
| [architecture/workflow_model.md](architecture/workflow_model.md) | Complete workflow execution blueprint for all sub-workflows |
| [architecture/state_and_memory_model.md](architecture/state_and_memory_model.md) | WorkflowState schema, ownership rules, memory service |
| [architecture/data_model.md](architecture/data_model.md) | All 19 SQLite table definitions (incl. `users`, ADR-062), indexing, JSON conventions |
| [architecture/config_model.md](architecture/config_model.md) | Hybrid config — YAML defaults + per-profile DB overrides (ADR-062), locked limits |
| [architecture/observability.md](architecture/observability.md) | 6-layer observability stack, event types, cost tracking |
| [architecture/hitl.md](architecture/hitl.md) | 7 HITL checkpoints, decision types, state transitions |
| [architecture/security.model.md](architecture/security.model.md) | PII minimization, untrusted input handling, ethics guardrails |
| [architecture/patterns.md](architecture/patterns.md) | 15 agentic AI patterns with implementation notes |
| [architecture/principles.md](architecture/principles.md) | 15 core architecture principles |
| [architecture/implementation_plan.md](architecture/implementation_plan.md) | Phased build plan — Phases 1–9, deliverables, tests, review gates |

### Architecture Decision Records
| Document | Purpose |
|---|---|
| [architecture/adr/ADR-000-index.md](architecture/adr/ADR-000-index.md) | Full index of all 52 ADRs |

ADRs cover every major design decision from v1/v2 separation (ADR-001) through Phase 9 cost optimization (ADR-051, ADR-052).

### Legal and Dependencies
| Document | Purpose |
|---|---|
| [disclaimer.md](disclaimer.md) | No-warranty statement, API cost responsibility, data source policies |
| [dependencies.md](dependencies.md) | All third-party libraries with versions and licence types |

---

## v1 Reference Documentation

The following documents describe the **v1 codebase** (`main.py`, `agents/`, `scrapers/`, `storage/`, `dashboard.py`). v1 remains stable and runnable. These docs are accurate for v1 — they do not describe the v2 system.

| Document | v1 Component |
|---|---|
| [main.md](main.md) | `main.py` — v1 CLI entry point |
| [dashboard.md](dashboard.md) | `dashboard.py` — v1 Streamlit dashboard |
| [architecture.md](architecture.md) | v1 architecture diagrams (Mermaid) |
| [agents/profile_agent.md](agents/profile_agent.md) | v1 `ProfileAgent` |
| [agents/scoring_agent.md](agents/scoring_agent.md) | v1 `ScoringAgent` |
| [agents/tailoring_agent.md](agents/tailoring_agent.md) | v1 `TailoringAgent` |
| [claude/client.md](claude/client.md) | v1 `ClaudeClient` |
| [claude/prompt_loader.md](claude/prompt_loader.md) | v1 `PromptLoader` |
| [claude/response_parser.md](claude/response_parser.md) | v1 `ResponseParser` |
| [models/job.md](models/job.md) | v1 `Job` data model |
| [models/profile.md](models/profile.md) | v1 `Profile` model |
| [models/config_schema.md](models/config_schema.md) | v1 config schema |
| [models/filters.md](models/filters.md) | Shared filter keywords (used by both v1 and v2) |
| [scrapers/adzuna.md](scrapers/adzuna.md) | v1 `AdzunaScraper` |
| [scrapers/linkedin.md](scrapers/linkedin.md) | v1 `LinkedInScraper` |
| [scrapers/ladders.md](scrapers/ladders.md) | v1 `LaddersScraper` |
| [scrapers/base.md](scrapers/base.md) | v1 base scraper |
| [storage/db.md](storage/db.md) | v1 SQLite schema |
| [prompts/overview.md](prompts/overview.md) | v1 prompt system |
