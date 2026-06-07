# Agentic AI Patterns — jobsearchagent v1 → v2

## Overview

This document defines the agentic AI patterns used in **jobsearchagent-v2**, traces each pattern's origin in v1, and explains how v2 evolved or replaced it.

The system is intentionally designed as a **controlled reasoning system** — not an autonomous agent system. Every pattern decision was made to keep the system predictable, testable, observable, and ethically grounded.

---

## v1 Foundation

v1 (`main.py` + three agents) established the core patterns that v2 built on. Understanding what v1 got right — and what it couldn't do — explains every v2 architecture decision.

### What v1 had

| Pattern | v1 Implementation |
|---|---|
| Structured Output | `ResponseParser` extracted JSON from Claude responses; Pydantic validated the result |
| Batched Fan-Out | `ScoringAgent` sent up to 10 jobs per Claude call; 3 parallel calls via `ThreadPoolExecutor` |
| Cache-Aside | `ProfileAgent` checked `data/profile.json` freshness by file modification timestamp |
| Prompt-as-Template | `PromptLoader.load("score_job", profile=..., tracks=...)` rendered prompts from template files |
| Pre-Filter Gate | Title exclusion + tech description keyword gates before any Claude call |
| Prompt Caching | System message was byte-identical across batches; `cache_control: ephemeral` on the first batch |
| Retry with Backoff | `tenacity` on all Claude and HTTP calls |
| Multi-Track Scoring | One Claude call scored IC, Architect, and Management simultaneously per batch |
| Pipeline State Machine | `Job.status`: NEW → SCORED → APPLIED → REJECTED/OFFER |

### What v1 could not do

| Missing Capability | Why it mattered |
|---|---|
| No workflow orchestration | `main.py` called agents sequentially — no state, no branching, no pause/resume |
| No human-in-the-loop | Everything ran automatically; `--tailor` was a separate CLI invocation |
| No reflection | Scoring and tailoring were single-pass — no self-correction, no quality evaluation |
| No research | Jobs were scored without company context; job descriptions alone drove all scores |
| No fidelity check | Tailoring produced output without verifying claims against the original resume |
| No observability | No per-call cost tracking, no event log, no run history beyond basic logging |
| No durable state | A crash mid-run lost all in-progress work; HITL was impossible |
| 3 agents total | Profile, Scoring, Tailoring — no specialization for review, advice, or coaching |

---

## Pattern Evolution Summary

| Pattern | v1 | v2 | Change |
|---|---|---|---|
| Structured Output | JSON extraction + optional Pydantic | All agents use Pydantic `BaseModel` + schema repair loop | Hardened |
| Cache-Aside | File mtime → flat JSON | Content hash (SHA-256) → SQLite row | Upgraded |
| Fan-Out | Batch 10 jobs per call, 3 parallel | One call per job, 5 concurrent workers with Research context | Redesigned |
| Prompt-as-Template | Keyword substitution | Guardrails injection + versioning + ephemeral cache on SystemMessage | Extended |
| Pre-Filter Gate | Title + tech description | Same, shared from `models/filters.py` | Preserved |
| Pipeline State Machine | Job.status (5 values) | WorkflowState (6 status × 15+ steps) | Expanded |
| Prompt Caching | Batch system prompt | Per-agent SystemMessage with `cache_control: ephemeral` | Extended |
| Retry with Backoff | `tenacity` on client | Schema repair loop + `tenacity` on provider | Extended |
| Workflow Orchestration | `main.py` sequential calls | LangGraph stateful graph + SqliteSaver | New |
| Bounded ReAct | — | Research Agent (MAX_RESEARCH_STEPS = 2) | New |
| Reflection Loop | — | Resume Critic → Review Auditor (MAX_REVIEW_ROUNDS = 2) | New |
| Evaluator/Critic | — | Review Auditor scores Critic output | New |
| Human-in-the-Loop | — | One active path: per-draft tailoring decisions (out-of-graph, ADR-055). The in-graph interrupt path was retired entirely (ADR-059) — the workflow runs end-to-end with no pause. | Reduced from original design |
| Evidence-Bound Generation | — | TailoringAgent: every claim requires `supporting_evidence`; page-budget + section_label + impact_rationale enforced (ADR-056) | New, then tightened |
| Guardrail Agent | — | FidelityReviewer validates claims, layout, rationale after every tailoring call | New |
| Observability | Basic logging | 6-layer event tracking, per-call cost, security events | New |
| Concurrent Scraping | — | ConcurrentAdzunaScraper (5 workers) | New |
| Live/Mock Mode Gate | — | ANTHROPIC_API_KEY presence → real vs mocked deps | New |
| Out-of-Graph Operations | `--tailor` CLI (no shared context) | On-demand tailoring router reads workflow checkpoint + repos; same agents, same fidelity contract (ADR-055). Extended to deep-review + interview-prep for ANY scored job, with deep-review-on-demand before tailoring (ADR-061) | New, then extended |
| Configurable Funnel Width | Fixed caps | `scoring.max_scored` (ceiling 25) + `search.max_discovered` (50) — system-wide default + per-run override, clamped in two places; the human owns the width inside a cost ceiling (ADR-061) | New |
| Pipeline Filter (input, not outcome) | v1 `excluded` flag on jobs | Restored as filter-only primitive (ADR-057); explicitly NOT application tracking | Restored from v1 |

---

## Pattern Detail

### 1. Workflow Orchestration

**v1:** `main.py` called agents in a fixed sequence. No shared state object. No conditional branching. No pause/resume. A crash lost all progress.

**v2:** LangGraph stateful graph. Each node reads from and writes to `WorkflowState`. `SqliteSaver` persists state after every node for durability, error recovery, and the ADR-060 two-phase scoring re-entry — the workflow itself runs end-to-end with no `interrupt()` pause (ADR-059). The orchestrator is the only code that updates state — agents return structured outputs, never mutate state directly.

```
v1: main.py → profile_agent.load() → scoring_agent.score_batch() → [done]

v2: LangGraph graph (configurable funnel width — ADR-061)
      register_run → discover_jobs (≤ max_discovered manual / max_scored auto)
        → load_resume
        → [optional manual scoring triage between phases, ADR-060]
        → [research + score concurrently, ≤ scoring.max_scored (≤25)]
        → auto-select qualifying jobs (ADR-054/059 — no HITL pause; top 3)
        → [deep_review per selected job, concurrent]
        → career_advisor → interview_coach (threshold-gated)
        → generate_report

      On-demand, post-workflow, per scored job (ADR-055/061):
      POST /workflows/{wf}/jobs/{job}/tailorings   → TailoringAgent → FidelityReviewer
        (deep-reviews on demand first if the job was never auto-selected)
      POST /workflows/{wf}/jobs/{job}/deep-review   → ResumeCritic + ReviewAuditor
      POST /workflows/{wf}/jobs/{job}/interview-prep → InterviewCoach
```

**Why it matters:** State-driven execution enables HITL, error recovery, and workflow introspection. The graph topology is testable independently of agent quality.

**References:** `app/workflows/workflow_graph.py` · ADR-002 · ADR-047

---

### 2. Supervisor / Router

**v1:** None. `main.py` called every agent unconditionally.

**v2:** The LangGraph orchestrator routes execution based on `WorkflowState`: skip deep review for low-scoring jobs, skip interview prep below the threshold, skip tailoring unless requested. Routing logic lives in the orchestrator — agents have no knowledge of what runs before or after them.

**Why it matters:** Centralizes all routing decisions. Agents stay simple and focused. Cost is controlled by not running agents unless conditions are met.

---

### 3. Static Planning (Macro Planning)

**v1:** Implicit — the sequence was hardcoded in `main.py`.

**v2:** Explicit — the LangGraph graph definition is the plan. The execution sequence is encoded in the graph topology, not in LLM outputs. An LLM never decides which agent to call next.

```
Plan is encoded once at startup.
Every run follows the same graph topology.
```

**Why it matters:** Predictable execution. The system cannot be made to run agents out of order or skip the fidelity reviewer by prompt manipulation.

**Reference:** ADR-009 (no formal multi-agent protocol for MVP)

---

### 4. Tool Use

**v1:** Agents called services directly (e.g., `db.update_job(job)`). No tool abstraction.

**v2:** Agents request tools — the orchestrator executes them. Agents never call the database, filesystem, or external URLs directly. Research Agent uses bounded tools: job content fetcher + description extractor.

**Why it matters:** Separates reasoning from execution. Prevents agents from taking unauthorized side effects. Tools are deterministic and testable independently.

**Reference:** ADR-006

---

### 5. Selective ReAct

**v1:** Not present. All Claude calls were single-shot.

**v2:** Used **only in the Research Agent**. The agent may take up to `MAX_RESEARCH_STEPS = 2` tool steps before producing its final `ResearchContext` output. Every other agent is single-shot structured output.

```
Thought → Action (fetch/extract) → Observation → [repeat ≤ 2] → ResearchContext
```

**Why the limit:** Unbounded ReAct in a high-volume agent (running for every job) would make costs and latency unpredictable. Two steps are sufficient to resolve ambiguous job descriptions.

**Why not global ReAct:** Giving every agent a reasoning loop adds complexity without proportional quality gain for structured tasks like scoring or fidelity checking.

**Reference:** ADR-010

---

### 6. Reflection Loop

**v1:** Not present. Tailoring and scoring were single-pass.

**v2:** The deep review workflow is a bounded critique-reflection loop:

```
Resume Critic produces ResumeReview
  → Review Auditor scores it (audit_score 0–100)
    → if audit_score < 75 AND improvement > 5 AND round < 2:
        Resume Critic runs again with auditor feedback
    → else: best review is persisted
```

The loop self-terminates on quality (`audit_score ≥ 75`), stagnation (< 5-point improvement across rounds), or the round cap (`MAX_REVIEW_ROUNDS = 2`).

**Why it matters:** A single-pass critique may miss gaps or produce generic analysis. The reflection loop catches weak reviews before they reach the user — without requiring human review of every round.

**Reference:** ADR-008

---

### 7. Evaluator / Critic

**v1:** Not present. No agent evaluated another agent's output.

**v2:** The Review Auditor is a dedicated evaluation agent. It receives the Resume Critic's review as input and produces a quality score plus specific failure modes: missing analysis, generic feedback, unsupported claims. Its output drives the reflection loop.

Model assignment: **Haiku** — quality evaluation is a checking task, not a generative one. The auditor reads existing text and applies criteria; it does not need Sonnet-level generation.

**Why it matters:** Without an evaluator, the only quality signal is user approval. The auditor introduces automated quality gating before any human review, reducing the amount of weak output the user needs to review.

---

### 8. Human-in-the-Loop (HITL)

**v1:** None. `--tailor` was a separate CLI invocation after reviewing terminal output. No in-workflow pause/resume.

**v2 (current state, post ADR-054 / ADR-055 / ADR-059):** The HITL surface deliberately collapsed from the original 7-checkpoint design as we learned what users actually wanted to control. The workflow now runs end-to-end with **no `interrupt()` pause** — the single active path is out-of-graph:

| Path | Status | What it does |
|------|--------|--------------|
| **Out-of-graph decisions** (per draft, ADR-055) | Active (only path) | After a workflow completes, the user generates drafts via `POST /workflows/{wf}/jobs/{job}/tailorings` and decides per draft via `POST /tailorings/{id}/decisions` with `approve / revise / reject / edit` (an `edit` is the human's own final draft). Decision lives on `tailored_resumes.decision` — no graph is paused for the call. The Resume Clinic (ADR-066) follows the same out-of-graph shape. |
| **In-graph interrupts** (`await_tailoring_approval`) | **Retired (ADR-059)** | The original design paused the graph (`status = waiting_for_user`, `pending_decision`, resume on `POST /workflows/{id}/decisions`). It was UI-dark for a long time and then removed outright in ADR-059 — the node, the `interrupt()`, and the `user_requested_tailoring` trigger are gone. Reintroduce only for a genuinely irreversible action (e.g. submitting an application). |

What was removed and why:

- **Job-selection HITL** (was checkpoint #1 in the original design): replaced by auto-select (ADR-054). Users found "pause to pick jobs" friction outweighed value when the threshold was already a meaningful filter.
- **In-graph tailoring approval**: retired in ADR-059 in favor of the out-of-graph decision above (a human `edit` makes the user the accountable author; the Fidelity Reviewer polices the agent, not the human).
- **Deep-review approval, interview-prep gate, fidelity-review resolution, report-export approval, application-status update**: never wired. The first four were design intent that the rest of the pipeline never demanded; the last is out of scope per CLAUDE.md ("no application tracking features").

**Key invariants that survived all the surface change:**
- The UI never auto-approves outputs.
- The backend validates every decision before persisting.
- The frontend renders state — it does not drive execution.
- Per-job exclusion (ADR-057) is *not* HITL — it's a filter input the user gives at any time, not a graph pause.

**References:** ADR-054 (auto-select replaced job HITL) · ADR-055 (out-of-graph tailoring decisions) · ADR-059 (retired the in-graph interrupt path; human `edit` decision) · ADR-057 (exclusion is filter, not HITL).

---

### 9. Structured Output

**v1:** `ResponseParser._strip_code_fences()` + `_extract_json()` + Pydantic validation. `TailoringAgent` bypassed Pydantic entirely (output was a plain dict). Manual error handling for malformed JSON.

**v2:** Every agent uses `ClaudeProvider.complete(schema=SomePydanticModel)`, which calls `model.with_structured_output(schema, include_raw=True)`. If parsing fails, a schema repair prompt is sent once before raising. No raw JSON extraction anywhere in the agent layer.

```
v1: raw string → _strip_code_fences → _extract_json → json.loads → Pydantic (sometimes)

v2: ChatAnthropic.with_structured_output(schema) → Pydantic always
                                                  → schema repair on first failure
```

**Why it matters:** Structured output failure is now a recoverable event, not a crash. The fidelity guardrail and observability layer both depend on every agent output being a valid Pydantic object.

**Reference:** ADR-007 · ADR-036

---

### 10. Bounded Execution

**v1:** `BATCH_SIZE = 10`, `MAX_PARALLEL_BATCHES = 3` in `scoring_agent.py`. No global LLM call budget.

**v2:** A set of hard limits enforced in `app/workflows/limits.py`. `check_budget()` is called before every LLM call in the workflow — if the run budget is exhausted, `BudgetExceededError` is raised and the workflow transitions to an error state.

```python
MAX_JOBS_PER_RUN       = 10   # default scored cap (per-run override up to MAX_SCORED_CEILING=25, ADR-061)
MAX_SELECTED_JOBS      = 3    # qualifying jobs that reach in-graph deep review (cost cut from 10)
MAX_RESEARCH_STEPS     = 2    # ReAct loop cap
MAX_REVIEW_ROUNDS      = 2    # reflection loop cap (cost cut from 3)
MAX_LLM_CALLS_PER_RUN  = 200  # global per-run budget backstop
```

**Why it matters:** A single misconfigured or adversarial run cannot spend unbounded API budget. Cost is first-class, not an afterthought.

**References:** ADR-041 · ADR-052 · ADR-054

---

### 11. Conditional Execution

**v1:** All scored jobs were shown in the terminal. Tailoring was a separate command. No conditional routing.

**v2:** Execution is gated at multiple points:
- Deep review runs **only for jobs that meet `min_match_score`** on any **active** track (the profile's `scoring.tracks` subset — ADR-071; ≤ `MAX_SELECTED_JOBS`, auto-selected — ADR-054)
- Interview Coach is **on-demand by default** (ADR-085) — the in-graph coach auto-fires only when `scoring.auto_interview_prep` is on; otherwise the user triggers it via `POST .../interview-prep`
- Tailoring runs **on user request only**, post-hoc via the out-of-graph tailoring router (ADR-055/059) — the in-graph tailoring node was retired
- Fidelity Reviewer runs **only after a generation agent** — every tailoring call and every Resume Clinic rewrite
- Relevance Filter runs **only when `search.relevance_filter` is on** (opt-in, ADR-079), as one cheap batched call before scoring

**Why it matters:** The most expensive agents (Sonnet for Critic, Coach, Tailoring) only run when there is a clear signal of value. Without conditional execution, a 10-job run would invoke every agent for all 10 jobs.

**Reference:** ADR-012 · ADR-014 · ADR-054 · ADR-055

---

### 12. Agent Specialization

**v1:** 3 general-purpose agents. `TailoringAgent` did both generation and output formatting. `ProfileAgent` handled both PDF parsing and caching.

**v2:** Specialized, single-responsibility agents — each with one prompt file, one output schema, and one pattern. The set has grown past the original eight as features landed (relevance pre-filter, Resume Clinic):

| Agent | Single Responsibility |
|---|---|
| Relevance Filter | Pre-scoring seniority/relevance triage (opt-in, ADR-079) |
| Research Agent | Company and role context extraction |
| Scoring Agent | Multi-track fitness scoring |
| Resume Critic | Gap identification and improvement suggestions |
| Review Auditor | Quality evaluation of the critic's output |
| Career Advisor | Cross-job career positioning synthesis |
| Interview Coach | Role-specific interview preparation |
| Tailoring Agent | Evidence-bound resume section rewriting |
| Resume Reviewer | Job-agnostic resume overhaul for the Resume Clinic (ADR-066) |
| Resume Chat | Iterative resume revision per chat turn (ADR-068) |
| Fidelity Reviewer | Claim validation against original resume |

**Why it matters:** Smaller prompts improve output quality. Single-responsibility agents are easier to test, tune, and replace independently. A failure in the Fidelity Reviewer does not affect the Scoring Agent.

---

### 13. Evidence-Bound Generation

**v1:** `TailoringAgent` produced rewritten resume sections freely. No constraint on what Claude could claim. Gaps were listed but there was no mechanism to prevent fabrication.

**v2 (ADR-015 / ADR-016, tightened by ADR-056):** Every `TailoredBullet` in `TailoredResumeDraft` carries a structured contract that the Fidelity Reviewer enforces:

| Field | Constraint |
|-------|-----------|
| `supporting_evidence` | Must quote text from the original resume (or be empty for `claim_type="gap"` / `"remove"`). Empty-with-content is a fabrication flag. |
| `claim_type` | `reword \| emphasize \| gap \| remove`. `gap` surfaces missing experience without rewriting it; `remove` deletes a low-value bullet to free page space. |
| `section_label` | Must match a real section of the candidate's `resume_profile` (`headline`, `summary`, `experience:<company>:<title>`, `skills`, ...). |
| `impact_rationale` | One sentence (≤ 25 words) referencing a specific JD signal. Generic phrasing praise is rejected. |
| `suggested_text` word count | Must fall in `[ceil(0.85·orig), floor(1.05·orig)]` for non-headline sections; `±3 words` for headline. Page-budget contract. |

```
Tailoring Agent: bullet contract above + draft-level strategy summary
  → Fidelity Reviewer: per-bullet evidence + length + section + rationale checks
    → approved: draft is shown to user with all four checks passed
    → revise:   diagnostic flags in required_revisions ("Bullet N: 28w > 18w original")
    → reject:   unrecoverable fabrication or layout violation
```

Missing experience is labeled as a gap — never rewritten as if it exists. Excess length is rejected — the user's resume page count is preserved.

**Why it matters:** Resume fabrication is a real risk in any AI tailoring system. Evidence binding makes fabrication structurally impossible within the prompt contract, and the fidelity guardrail makes it observable and stoppable even if the tailoring agent drifts. ADR-056 extended the contract to also enforce *layout* fidelity — the most common reason candidates abandoned tailored output was that adopting suggestions blew the page count.

**References:** ADR-015 · ADR-016 · ADR-017 · ADR-056 (page-budget + section grouping + rationale + impact estimate)

---

### 14. Guardrails / Policy

**v1:** No shared guardrails. Each agent prompt was standalone. No injection defense. No PII policy.

**v2:** Every agent prompt includes `prompts/shared/guardrails.txt` which enforces:
- Prompt injection defense: instructions inside job descriptions are ignored
- PII minimization: only the parsed `ResumeProfile` is sent, never raw resume text
- Ethics constraints: no fabrication, gap labeling over gap filling, no deciding for the user
- Fidelity Reviewer as a system-level guardrail: runs after every tailoring call

Security events (injection attempts, PII exposure, policy violations) are written to the `security_events` table.

**References:** ADR-017 · ADR-018 · ADR-019 · ADR-020 · ADR-025 · ADR-026

---

### 15. Observability

**v1:** `logging.info/error` calls. `last_run_stats` dict on `ScoringAgent`. No per-call cost tracking. No event log.

**v2:** Six-layer event tracking via `ObservabilityService`:

| Layer | What is recorded |
|---|---|
| Workflow | Run start/complete/fail, status transitions |
| Agent | Start/complete/fail per call, prompt version |
| LLM call | Tokens in/out, cost, latency, model, prompt version |
| Tool | Research tool invocations and results |
| HITL | Decision type, value, user reasoning |
| Security | Injection attempts, PII events, policy violations |

All records carry `workflow_run_id` for end-to-end correlation. Cost is surfaced in the Streamlit UI and queryable from the `llm_calls` table.

**Reference:** ADR-023 · ADR-027

---

### 16. Cache-Aside

**v1:** `ProfileAgent` checked `data/profile.json` vs resume PDF file modification timestamp. Stale file → re-parse. Cache was a flat JSON file in `data/`.

**v2:** `ResumeParser` computes SHA-256 of the PDF text. Cache key is the hash. On cache hit, the stored `ResumeProfile` row is returned immediately with no LLM call. Cache is a row in the `resumes` SQLite table — same database as all other application data.

```
v1: cache key = file mtime (position in time)
v2: cache key = SHA-256(pdf text) (content identity)
```

**Why the upgrade:** File mtime is unreliable — touching a file invalidates the cache even if content didn't change. Content-based keys ensure the cache is only invalidated when the resume actually changes.

---

### 17. Prompt Caching

**v1:** `ScoringAgent._score_chunk()` constructed a byte-identical system prompt across all batches (job count excluded from the system message deliberately). Marked with `cache_control: {"type": "ephemeral"}`.

**v2:** `PromptLoader.assemble()` wraps the system message (guardrails + agent prompt) in a `cache_control: ephemeral` block for every agent call. The system message is static per agent per session — after the first call, all subsequent calls for that agent hit the cache at 10% of normal input cost.

```
v1: one cached prompt per run (ScoringAgent only)
v2: one cached prompt per agent per 5-minute window (every agent)
```

**Reference:** ADR-024 (prompt versioning)

---

### 18. Concurrent Fan-Out

**v1:** `ScoringAgent` batched 10 jobs into a single Claude call, running up to 3 batches concurrently. Parallelism was at the batch level — more efficient per token, but no per-job research context.

**v2:** Each job is processed individually: Research Agent call → Scoring Agent call. Both run for all jobs concurrently via `ThreadPoolExecutor(max_workers=5)`. Research context is injected into the scoring prompt, improving score accuracy.

```
v1: [job1..10] → one Claude call (batch)   ×3 parallel calls
v2: [job1] [job2] ... [job10]              ×5 parallel workers
    each: ResearchAgent → ScoringAgent
```

**Tradeoff:** v1 was more token-efficient (one call, 10 jobs). v2 uses more calls but produces higher-quality per-job scores because the Research Agent enriches each job before scoring.

**Extended to deep_review (post-9):** The `deep_review` node uses the same template — a `_review_one(job)` worker that handles one job's full critic+auditor reflection loop, dispatched across `_DEEP_REVIEW_WORKERS=5` threads via `ThreadPoolExecutor` + `as_completed`. The speedup scales with how many jobs qualify (`MAX_SELECTED_JOBS=3` in-graph, ADR-061; was 10 under ADR-054). Budget is pre-flighted at `MAX_REVIEW_ROUNDS * 2` calls per job (worst case). Final-review semantics preserved: walk `selected_jobs` in input order and pick the last best_review (matches the previous "last writer wins" behaviour).

**References:** ADR-049 · ADR-039 · ADR-054

---

### 19. Out-of-Graph Operations

**v1:** Tailoring was a separate CLI invocation (`--tailor`), independent of the scoring run. No coupling, but also no shared context.

**v2:** Most agent work runs as nodes inside the LangGraph workflow, with state managed by `SqliteSaver`. But some operations are inherently post-hoc and per-job — the user wants to invoke them selectively after seeing earlier output, sometimes for multiple jobs from the same run, sometimes hours or days later. Forcing those into the graph would mean either re-entering a finished workflow (interrupts don't fit) or pre-declaring intent that the user doesn't yet have.

The **out-of-graph operation** pattern: expose the same agents as a small REST surface that reads the workflow state from the LangGraph checkpoint and persists results to the relational tables, without going through the state machine. The agents, prompts, schemas, and fidelity invariants are identical to the in-graph version.

```
in-graph:    discover → score → ... → auto-select → deep_review → report (no tailoring node)
out-of-graph: POST /workflows/{wf}/jobs/{job}/tailorings → TailoringAgent → FidelityReviewer → tailored_resumes
```

Currently used for: on-demand tailoring, deep review, and interview prep for any scored job (ADR-055/061), plus the standalone Resume Clinic (ADR-066). Decisions are recorded as a column on the relevant table, not as a graph interrupt — there is no graph paused for the decision.

**Why it matters:** Workflow completion is a discrete event; user intent isn't. Tying every agent call to a graph run forces lifetimes that don't actually share. The out-of-graph pattern preserves the structural invariants (evidence binding, fidelity check) while decoupling trigger from workflow state.

**Reference:** ADR-055

---

### 20. Live/Mock Mode Gate

**v1:** Not present. All runs used real API keys; the test suite was structured to avoid agent calls.

**v2:** At startup, `app/api/dependencies.py` checks `ANTHROPIC_API_KEY`:
- Present → `_build_real_deps()`: real `ClaudeProvider`, `SqliteSaver`, real scrapers
- Absent → `_build_mocked_deps()`: all agents mocked, `MemorySaver`

The graph topology is identical in both modes. The full test suite runs in mock mode — no API keys required for CI.

**Why it matters:** Makes the system testable without API credentials and enables engineers to develop the UI and API layer without incurring LLM costs.

**Reference:** ADR-048

---

### 21. Pipeline Filter (Filter Input, Not Outcome Tracking)

**v1:** `dashboard.py::exclude_jobs_db` flipped an `excluded` flag on the `jobs` table; analytics views joined `WHERE excluded = 0`. Survived the lifetime of v1.

**v2 (ADR-057):** Restored the same shape after dropping it during the v2 scope reset. The CLAUDE.md "no application tracking features" rule had inadvertently swept it out — but exclusion is fundamentally different from application tracking:

| Concern | What it captures | Direction | Allowed? |
|---|---|---|---|
| **Pipeline filter** (this pattern) | "Hide this from my views and stop processing it" | Signal user gives TO the system | ✓ |
| **Application tracking** | Apply date, recruiter, status transitions | Outcomes the system records ABOUT the user | ✗ — out of scope per CLAUDE.md |

The schema captures only filter-shaped fields (`excluded`, `excluded_reason`, `excluded_at`). It does NOT capture `applied_at`, `application_status`, or any other behavior outcome. ADR-057 includes a code-review table that makes the line easy to police on future PRs.

The cost-saving payoff comes for free via the existing dedup logic: `JobDiscoveryService.deduplicate()` already drops re-discovered URLs at `url_exists()`. An excluded job leaves its row in the DB; a future Adzuna run that surfaces the same URL with a fresh `source_job_id` is dropped before scoring without any extra logic.

**Why it matters as a separate pattern:** Several obvious-looking features (saved searches, "remind me about this in 30 days", custom company block lists) are tempting to add but each is one step closer to becoming an ATS. Naming the filter-vs-tracker distinction up front makes it easier to evaluate future feature requests against the boundary.

**References:** ADR-057 · CLAUDE.md "No application tracking features"

---

## Pattern Strategy

### What v1 Proved

- Structured output + Pydantic validation is the right foundation — v2 hardened it, not replaced it
- Prompt caching pays for itself immediately — carried forward unchanged
- Pre-filter gate before any LLM call is essential for cost control — carried forward unchanged
- Concurrent execution of independent tasks is correct — v2 generalized it across the workflow
- Cache-aside for expensive parsing operations is worth the complexity — upgraded to content-hash

### What v2 Changed

- **Single-pass → reflection loop**: quality requires iteration, not just better prompts
- **Implicit pipeline → explicit state machine**: HITL is impossible without durable, inspectable state
- **3 general agents → specialized, single-responsibility agents**: one responsibility per agent enables targeted improvement
- **Free-form generation → evidence-bound generation**: ethical AI requires structural constraints, not just ethical prompts
- **Trust then verify → verify always**: the Fidelity Reviewer runs unconditionally after every tailoring call

### What Was Avoided (and Why)

| Avoided | Reason |
|---|---|
| Global ReAct | Unpredictable cost and latency at scale; single-shot structured output is sufficient for all non-research tasks |
| Fully autonomous agents | Users must own career decisions; the system informs, it does not decide |
| Dynamic planning (LLM-chosen next step) | Adds failure modes without proportional benefit when the plan is known in advance |
| Multi-agent protocol (A2A messaging) | Premature complexity for a sequential, single-run workflow; orchestrator-mediated is sufficient (ADR-009) |

---

## Future Evolution

Already shipped from the original "future" list:

| Pattern | Where it landed |
|---|---|
| Parallel deep review | ADR-054 — `_DEEP_REVIEW_WORKERS=5` thread pool; section 18 |
| Multi-provider support | ADR-053 — `ModelRegistry` with per-agent assignment via Settings UI |

Items still ahead, in order of value:

| Pattern | Trigger to introduce |
|---|---|
| Memory-driven personalization | After observability is mature and run history data is sufficient to validate suggestions. Per-job exclusion (ADR-057) is the first piece of feedback signal the system captures — natural seed for "users who exclude X tend to also exclude Y" inference. |
| Per-suggestion accept/reject in tailoring | Currently a draft is approved as a whole. Per-suggestion decisions need a new `tailored_resumes.suggestion_decisions_json` column and a UI redesign — captured as a follow-up to ADR-056. |
| Iterative-revision context for tailoring | Calling `trigger_tailoring` again produces an amnesiac fresh draft. Carrying prior accept/reject decisions and a free-text revise note into the next call would close the loop. Depends on per-suggestion decisions above. |
| Adaptive workflow routing | After the static plan has proven stable across 50+ real runs. |
| Domain-aware ScoringAgent | The `domain_score` field is wired but the agent doesn't yet have JD-domain heuristics. After more runs land. |

Introduce each only after: observability is mature, evaluation framework is established, and the existing workflow is stable.
