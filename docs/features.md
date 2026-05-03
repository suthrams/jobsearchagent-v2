# Job Search Agent v2 — Features & Capabilities

A multi-agent career intelligence system: discovers jobs, scores fit across three career tracks, reviews high-match roles in depth, prepares you for interviews, and tailors your resume — all via a FastAPI backend and Streamlit UI powered by Claude.

---

## Table of Contents

1. [Job Discovery](#1-job-discovery)
2. [Pre-Filter Gate](#2-pre-filter-gate)
3. [Company Research](#3-company-research)
4. [Job Scoring — Three Career Tracks](#4-job-scoring--three-career-tracks)
5. [Deep Review — Critic + Auditor Loop](#5-deep-review--critic--auditor-loop)
6. [Career Advice](#6-career-advice)
7. [Interview Preparation](#7-interview-preparation)
8. [Resume Tailoring + Fidelity Review](#8-resume-tailoring--fidelity-review)
9. [Human-in-the-Loop Checkpoints](#9-human-in-the-loop-checkpoints)
10. [Observability & Cost Tracking](#10-observability--cost-tracking)
11. [Workflow Checkpointing](#11-workflow-checkpointing)
12. [Configuration](#12-configuration)
13. [Backend API](#13-backend-api)
14. [Feature Summary](#14-feature-summary)

---

## 1. Job Discovery

Jobs are discovered from multiple sources concurrently on every run.

### Adzuna (automated, concurrent)
- Searches a configurable list of job titles across multiple cities simultaneously via `ConcurrentAdzunaScraper` (5 workers)
- Separate keyword list for US-wide remote searches
- Configurable search radius in kilometres
- Free-tier quota: `(titles × locations) + remote_keywords` — quota commentary in config keeps calls under 100/day

### LinkedIn (manual intake)
- LinkedIn blocks automated scraping — paste job posting URLs into `data/linkedin_inbox.txt` (one per line)
- The agent fetches each URL, extracts title/company/description, and clears the file so the same URL is never processed twice

### Deduplication
- Jobs are deduplicated by URL and by title + company (case-insensitive) across all sources

### Volume cap
- `MAX_JOBS_PER_RUN = 10` — ensures predictable cost per run

---

## 2. Pre-Filter Gate

Two filter layers remove noise jobs before any LLM calls are made.

### Scraper-level filter (applied at scrape time)
- **Title relevance gate** — job must contain a keyword from `RELEVANT_TITLE_KEYWORDS`
- **Title exclusion gate** — jobs with titles in `EXCLUDED_TITLE_KEYWORDS` are dropped (property manager, sales engineer, intern, etc.)

### Scoring-level filter (applied before Research + Scoring)
- **Title exclusion** — catches any noise that bypassed the scraper filter
- **Tech description gate** — description must contain at least one keyword from `TECH_DESCRIPTION_KEYWORDS`
- **Staleness gate** — jobs older than `staleness.max_days` are skipped

Both layers share a single module (`models/filters.py`) so the keyword lists are always in sync.

---

## 3. Company Research

Every job passes through the Research Agent before scoring.

- Pattern: **Bounded ReAct** — the agent may take up to `MAX_RESEARCH_STEPS = 2` tool steps
- Tools: job content fetcher, description extractor
- Output: `ResearchContext` — company summary, role context, technology signals, leadership signals, domain signals, risk flags, confidence score
- Model: **Haiku** (high-volume: runs for every job)

Research context is injected into the Scoring Agent prompt, improving score accuracy for roles where the job description alone is sparse.

---

## 4. Job Scoring — Three Career Tracks

Each job is scored against your resume independently on three career tracks.

| Track | Target Roles |
|---|---|
| `ic` | Senior / Staff / Principal Engineer |
| `architect` | Solutions / Principal / Enterprise Architect |
| `management` | Senior Manager / Director / Head of Engineering / VP |

### How scoring works
- All 10 jobs are scored **concurrently** via `ThreadPoolExecutor` (5 workers) — wall-clock time ~20s for 10 jobs
- Each job receives an overall score (0–100) plus sub-scores: technical, architecture, leadership, domain
- Output includes: match summary, strengths, gaps, recommended next action, confidence
- Model: **Haiku** (already the cheapest model; concurrent execution keeps latency low)
- Only tracks enabled in `config.yaml` are scored

### Score-driven routing
After scoring, every job whose best track score (`max(technical_score, architecture_score, leadership_score)`) meets `effective_config.scoring.min_match_score` (default 75) auto-advances to deep review, up to `MAX_SELECTED_JOBS = 10` (= `MAX_JOBS_PER_RUN`). Job-selection HITL was removed in the v2 usability refactor; the cap was raised from 3 to 10 in ADR-054.

---

## 5. Deep Review — Critic + Auditor Loop

For each shortlisted job, a reflection loop produces a thorough resume review.

### Resume Critic
- Pattern: **Critique**
- Input: job posting + resume profile + research context + job score
- Output: `ResumeReview` — section-by-section analysis, critical gaps (resume gaps vs career gaps distinguished), suggested improvements, questions for the user
- Model: **Sonnet** (deep analysis; quality-sensitive)

### Review Auditor
- Pattern: **Evaluator / Reflection**
- Input: critic's review + job posting
- Output: `ReviewAudit` — audit score (0–100), quality summary, missing analysis points, generic feedback flags, unsupported claims
- Model: **Haiku** (validation/checking task)

### Reflection loop
- Loop runs until: `audit_score ≥ AUDIT_QUALITY_THRESHOLD (75)` OR stagnation (< 5-point improvement) OR `MAX_REVIEW_ROUNDS = 3`
- Best review across all rounds is persisted

---

## 6. Career Advice

After the deep review loop, the Career Advisor synthesizes findings across all shortlisted jobs.

- Pattern: **Advisory**
- Input: all job scores + all reviews + resume profile
- Output: `CareerAdvice` — track recommendations, positioning strategy, skill gap priorities, suggested timeline
- Model: **Sonnet** (generative advisory prose)
- Runs once per workflow run, not per job

---

## 7. Interview Preparation

Conditional interview coaching for roles that meet the threshold.

- Triggered when: `match_score ≥ INTERVIEW_COACH_THRESHOLD (75)` OR explicit user request at the HITL checkpoint
- Output: `InterviewPrep` — likely questions, suggested answers, topics to research, red flags to address
- Model: **Sonnet** (generative coaching content)

---

## 8. Resume Tailoring + Fidelity Review

Evidence-bound resume tailoring with a mandatory fidelity guardrail.

### Tailoring Agent
- Pattern: **Evidence-bound generation**
- Every tailored claim must include `supporting_evidence` referencing the original resume
- Missing experience is labeled as a gap — never rewritten as if present
- Output: `TailoredResumeDraft` — section rewrites with evidence citations, identified gaps
- Model: **Sonnet** (quality-critical; user acts on this output)

### Fidelity Reviewer
- Pattern: **Validation / Guardrail**
- Runs automatically after every Tailoring Agent call — cannot be bypassed
- Flags any claim in the draft that is unsupported by the original resume
- Output: `FidelityReview` — pass/fail per claim, flagged fabrications, overall verdict
- Model: **Haiku** (binary verification task)

The Fidelity Reviewer must clear the draft before it is persisted or shown to the user.

---

## 9. Human-in-the-Loop Checkpoints

The workflow pauses at seven points for user decisions. The backend sets `status = waiting_for_user` and records a `pending_decision` before each pause.

| Checkpoint | Decision |
|---|---|
| Job Selection | Which shortlisted jobs to deep-review |
| Deep Review Approval | Accept review or request another round |
| Interview Prep Decision | Proceed with coaching or skip |
| Tailoring Approval | Accept tailored draft or reject |
| Fidelity Review Resolution | Accept flagged claims or override |
| Report Export Approval | Confirm before generating report |
| Application Status Update | Mark job as applied / rejected / offer |

All decisions are validated by the backend before the workflow resumes. The UI never auto-approves outputs.

---

## 10. Observability & Cost Tracking

Every meaningful event is recorded across six layers.

| Layer | What is recorded |
|---|---|
| Workflow | Run start/complete/fail, status transitions |
| Agent | Start/complete/fail per agent call |
| LLM call | Token counts, cost, latency, model, prompt version |
| Tool | Research tool invocations and results |
| HITL | Decision type, value, user reasoning |
| Security | Prompt injection attempts, PII events, policy violations |

### Cost visibility
- `estimated_cost_usd` accumulated per LLM call, per run
- Surfaced in the Streamlit UI run history view
- Query the `llm_calls` table directly for per-agent breakdown

### Prompt caching
- System messages use `cache_control: ephemeral` — 90% cost reduction on repeated agent calls within a 5-minute window (Anthropic ephemeral cache)

---

## 11. Workflow Checkpointing

LangGraph `SqliteSaver` checkpoints workflow state after every node execution.

- HITL pause/resume survives backend restarts
- Interrupted runs can be resumed from the last completed node
- Checkpoint data stored in `data/v2.db` alongside all application tables

In mock mode (no `ANTHROPIC_API_KEY`), `MemorySaver` is used instead — suitable for development and testing.

---

## 12. Configuration

### config/config.yaml — static defaults

```yaml
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

### User-configurable at runtime (via UI → DB)
Search titles, locations, salary, work mode, career tracks, Adzuna settings.

### Locked (never user-configurable)
LLM model assignments, execution limits (`MAX_JOBS_PER_RUN`, `MAX_REVIEW_ROUNDS`, etc.), safety thresholds, cost caps.

---

## 13. Backend API

The FastAPI backend exposes REST endpoints for all workflow operations.

```bash
uvicorn app.api.main:app --reload   # starts at http://localhost:8000
# Swagger UI: http://localhost:8000/docs
```

Key endpoint groups:
- `POST /workflow/run` — start a new workflow run
- `GET /workflow/{run_id}/status` — poll run status and current step
- `POST /workflow/{run_id}/decision` — submit a HITL decision to resume the workflow
- `GET /jobs` — list scored jobs with filters
- `GET /reports/{run_id}` — retrieve the final report

The Streamlit UI calls these endpoints. You can also drive the workflow directly via the API or notebooks.

---

## 14. Feature Summary

| Capability | Status |
|---|---|
| Multi-source job discovery (Adzuna, LinkedIn) | ✅ |
| Concurrent Adzuna scraping (5 workers) | ✅ |
| Two-layer pre-filter gate (title + description) | ✅ |
| Company research — bounded ReAct agent | ✅ |
| Concurrent job scoring — 3 career tracks (5 workers) | ✅ |
| Deep review — critic + auditor reflection loop (≤ 3 rounds) | ✅ |
| Resume gap vs career gap distinction | ✅ |
| Career advice — cross-job positioning synthesis | ✅ |
| Interview coaching — conditional on match score | ✅ |
| Evidence-bound resume tailoring | ✅ |
| Fidelity guardrail — blocks fabricated claims | ✅ |
| 7 human-in-the-loop checkpoints | ✅ |
| Workflow checkpointing — HITL pause/resume across restarts | ✅ |
| 6-layer observability (workflow, agent, LLM, tool, HITL, security) | ✅ |
| Per-run and per-call cost tracking | ✅ |
| Prompt caching (~90% cost reduction on cache hits) | ✅ |
| Model tiering — Haiku for volume/validation, Sonnet for generative | ✅ |
| Hybrid configuration — YAML defaults + DB overrides | ✅ |
| FastAPI backend + Streamlit UI | ✅ |
| SQLite persistence — 17 tables | ✅ |
| Test suite — 389 tests, mock mode, no real API calls in CI | ✅ |
