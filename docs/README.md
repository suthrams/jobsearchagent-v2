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

## Retained shared libraries (formerly v1) — ADR-063

The v1 runtime was **removed** in ADR-063. A small set of v1 modules are kept
because v2 imports them; these docs describe code that is still present and live:

| Document | Module (still present) |
|---|---|
| [models/job.md](models/job.md) | `models/job.py` — `Job` / `JobSource` / `SalaryRange`, used by the scrapers |
| [models/config_schema.md](models/config_schema.md) | `models/config_schema.py` — `AdzunaConfig` |
| [models/filters.md](models/filters.md) | `models/filters.py` — shared title/description keyword filters |
| [scrapers/adzuna.md](scrapers/adzuna.md) | `scrapers/adzuna.py` — wrapped by v2 `ConcurrentAdzunaScraper` |
| [scrapers/linkedin.md](scrapers/linkedin.md) | `scrapers/linkedin.py` — built by `app/api/dependencies.py` |
| [scrapers/base.md](scrapers/base.md) | `scrapers/base.py` — base scraper |

## Removed v1 documentation

The doc pages for the retired v1 runtime were **deleted** along with the code
(ADR-063), since they described modules that no longer exist: `main.md`,
`dashboard.md`, `architecture.md`, `agents/*`, `claude/*`, `models/profile.md`,
`scrapers/ladders.md`, `storage/db.md`, `prompts/*`. They remain recoverable from
git history before the ADR-063 commit if ever needed.
