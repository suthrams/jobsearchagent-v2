# Job Search Agent — Documentation Index

This folder contains per-file documentation for every module in the project.
Each doc covers purpose, public interface, data flow, and the agentic AI pattern it demonstrates (where applicable).

## Table of Contents

### Legal and Dependencies
| Document | Purpose |
|---|---|
| [Disclaimer and Usage Terms](disclaimer.md) | No-warranty statement, API cost responsibility, data source policies, personal data handling |
| [Third-Party Dependencies](dependencies.md) | All open source libraries with versions and licence types |

---

### Feature Overview
See [features.md](features.md) for a complete summary of every feature and capability — scraping, filtering, scoring, dashboard, exclusion, tailoring, cost tracking, and CLI commands.

### User Guide
See [user_guide.md](user_guide.md) for the full end-to-end walkthrough — setup, daily workflow, reading results, tailoring, and troubleshooting.

---

### Architecture Diagrams
See [architecture.md](architecture.md) for all Mermaid diagrams — renders on GitHub.

| Diagram | What it shows |
|---|---|
| [Solution Architecture Overview](architecture.md#0-solution-architecture--high-level-overview) | High-level: inputs → pipeline → outputs |
| [System Architecture](architecture.md#1-system-architecture--component-overview) | 5-layer component block diagram |
| [Main Run Control Flow](architecture.md#2-main-run--control-flow) | Scrape → filter → score → display |
| [Cache-Aside Pattern](architecture.md#3-agentic-pattern-cache-aside-profileagent) | ProfileAgent sequence diagram |
| [Batched Fan-Out Pattern](architecture.md#4-agentic-pattern-batched-fan-out-scoringagent) | ScoringAgent sequence diagram |
| [Structured Output Pipeline](architecture.md#5-agentic-pattern-structured-output-pipeline) | Raw text → typed object |
| [Job Lifecycle State Machine](architecture.md#6-job-lifecycle--pipeline-state-machine) | NEW → SCORED → APPLIED → OFFER |
| [Resume Tailoring Flow](architecture.md#7-resume-tailoring--sequence-diagram) | --tailor command sequence |
| [Prompt-as-Template Pattern](architecture.md#8-prompt-as-template-pattern) | Prompt file → Claude API |
| [Pre-Filter Gate Pattern](architecture.md#9-pre-filter-gate-pattern) | 4-stage job filter before Claude |
| [Agentic Patterns Summary](architecture.md#10-agentic-patterns-summary) | Mind map of all patterns |

---

### Entry Points
| File | Doc | Purpose |
|---|---|---|
| [main.py](../main.py) | [main.md](main.md) | CLI entry point — scrape, score, list, tailor |
| [dashboard.py](../dashboard.py) | [dashboard.md](dashboard.md) | Streamlit browser dashboard |

### Agents
| File | Doc | Purpose |
|---|---|---|
| [agents/profile_agent.py](../agents/profile_agent.py) | [agents/profile_agent.md](agents/profile_agent.md) | PDF resume → structured Profile (with caching) |
| [agents/scoring_agent.py](../agents/scoring_agent.py) | [agents/scoring_agent.md](agents/scoring_agent.md) | Batch-scores jobs against profile via Claude |
| [agents/tailoring_agent.py](../agents/tailoring_agent.py) | [agents/tailoring_agent.md](agents/tailoring_agent.md) | Rewrites resume sections for a specific job |

### Claude Layer
| File | Doc | Purpose |
|---|---|---|
| [claude/client.py](../claude/client.py) | [claude/client.md](claude/client.md) | Anthropic SDK wrapper — all API calls go here |
| [claude/prompt_loader.py](../claude/prompt_loader.py) | [claude/prompt_loader.md](claude/prompt_loader.md) | Loads and renders prompt templates |
| [claude/response_parser.py](../claude/response_parser.py) | [claude/response_parser.md](claude/response_parser.md) | Extracts and validates JSON from Claude responses |

### Models
| File | Doc | Purpose |
|---|---|---|
| [models/job.py](../models/job.py) | [models/job.md](models/job.md) | Core Job data model and lifecycle enums |
| [models/profile.py](../models/profile.py) | [models/profile.md](models/profile.md) | Candidate profile extracted from resume |
| [models/config_schema.py](../models/config_schema.py) | [models/config_schema.md](models/config_schema.md) | Pydantic schema for config.yaml |

### Scrapers
| File | Doc | Purpose |
|---|---|---|
| [scrapers/base.py](../scrapers/base.py) | [scrapers/base.md](scrapers/base.md) | Abstract base class for all scrapers |
| [scrapers/adzuna.py](../scrapers/adzuna.py) | [scrapers/adzuna.md](scrapers/adzuna.md) | Adzuna REST API scraper |
| [scrapers/linkedin.py](../scrapers/linkedin.py) | [scrapers/linkedin.md](scrapers/linkedin.md) | LinkedIn manual URL intake scraper |
| [scrapers/ladders.py](../scrapers/ladders.py) | [scrapers/ladders.md](scrapers/ladders.md) | Ladders.com HTML scraper |

### Storage
| File | Doc | Purpose |
|---|---|---|
| [storage/db.py](../storage/db.py) | [storage/db.md](storage/db.md) | SQLite persistence layer for Job objects |

### Prompts
| File | Doc |
|---|---|
| [prompts/](../prompts/) | [prompts/overview.md](prompts/overview.md) |

---

## Agentic AI Patterns at a Glance

| Pattern | Where |
|---|---|
| Structured Output (JSON + Pydantic) | `claude/response_parser.py` + every agent |
| Cache-Aside | `agents/profile_agent.py` |
| Batched Fan-Out | `agents/scoring_agent.py` |
| Prompt-as-Template | `claude/prompt_loader.py` + `prompts/` |
| Pre-Filter Gate | `agents/scoring_agent.py`, `scrapers/adzuna.py` |
| Pipeline State Machine | `models/job.py` (ApplicationStatus) |
| Retry with Exponential Backoff | `claude/client.py`, all scrapers |
| Multi-Track Scoring | `agents/scoring_agent.py` + `prompts/score_job.md` |
