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
| Reflection Loop | — | Resume Critic → Review Auditor (MAX_REVIEW_ROUNDS = 3) | New |
| Evaluator/Critic | — | Review Auditor scores Critic output | New |
| Human-in-the-Loop | — | 7 checkpoints, state pause/resume via SqliteSaver | New |
| Evidence-Bound Generation | — | TailoringAgent: every claim requires `supporting_evidence` | New |
| Guardrail Agent | — | FidelityReviewer validates claims after every tailoring call | New |
| Observability | Basic logging | 6-layer event tracking, per-call cost, security events | New |
| Concurrent Scraping | — | ConcurrentAdzunaScraper (5 workers) | New |
| Live/Mock Mode Gate | — | ANTHROPIC_API_KEY presence → real vs mocked deps | New |

---

## Pattern Detail

### 1. Workflow Orchestration

**v1:** `main.py` called agents in a fixed sequence. No shared state object. No conditional branching. No pause/resume. A crash lost all progress.

**v2:** LangGraph stateful graph. Each node reads from and writes to `WorkflowState`. `SqliteSaver` persists state after every node so HITL pause/resume survives process restarts. The orchestrator is the only code that updates state — agents return structured outputs, never mutate state directly.

```
v1: main.py → profile_agent.load() → scoring_agent.score_batch() → [done]

v2: LangGraph graph
      discover_jobs → load_resume → [research + score concurrently]
        → HITL: job selection
        → deep_review loop → career_advisor → interview_coach
        → HITL: tailoring approval
        → tailoring → fidelity_review → report
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
    → if audit_score < 75 AND improvement > 5 AND round < 3:
        Resume Critic runs again with auditor feedback
    → else: best review is persisted
```

The loop self-terminates on quality (`audit_score ≥ 75`), stagnation (< 5-point improvement across rounds), or the round cap (`MAX_REVIEW_ROUNDS = 3`).

**Why it matters:** A single-pass critique may miss gaps or produce generic analysis. The reflection loop catches weak reviews before they reach the user — without requiring human review of every round.

**Reference:** ADR-008

---

### 7. Evaluator / Critic

**v1:** Not present. No agent evaluated another agent's output.

**v2:** The Review Auditor is a dedicated evaluation agent. It receives the Resume Critic's review as input and produces a quality score plus specific failure modes: missing analysis, generic feedback, unsupported claims. Its output drives the reflection loop.

Model assignment: **Haiku** — quality evaluation is a checking task, not a generative one. The auditor reads existing text and applies criteria; it does not need Sonnet-level generation.

**Why it matters:** Without an evaluator, the only quality signal is user approval. The auditor introduces automated quality gating before the HITL checkpoint, reducing the amount of weak output the user needs to review.

---

### 8. Human-in-the-Loop (HITL)

**v1:** None. `--tailor` was a separate CLI invocation after reviewing terminal output. No in-workflow pause/resume.

**v2:** Seven explicit checkpoints embedded in the workflow. At each checkpoint:
1. Backend sets `WorkflowState.status = waiting_for_user` and writes `pending_decision`
2. SqliteSaver persists the paused state
3. Backend serves the pending decision to the UI
4. User submits a decision via the API
5. Backend validates the decision and resumes the graph from the checkpoint

```
HITL checkpoints:
  1. Job Selection (after scoring)
  2. Deep Review Approval
  3. Interview Prep Decision
  4. Tailoring Approval
  5. Fidelity Review Resolution
  6. Report Export Approval
  7. Application Status Update
```

**Key constraint:** The UI never auto-approves outputs. The backend validates every decision before resuming. The frontend renders state — it does not drive execution.

**References:** ADR-011 · ADR-047

---

### 9. Structured Output

**v1:** `ResponseParser._strip_code_fences()` + `_extract_json()` + Pydantic validation. `TailoringAgent` bypassed Pydantic entirely (output was a plain dict). Manual error handling for malformed JSON.

**v2:** All 8 agents use `ClaudeProvider.complete(schema=SomePydanticModel)`, which calls `model.with_structured_output(schema, include_raw=True)`. If parsing fails, a schema repair prompt is sent once before raising. No raw JSON extraction anywhere in the agent layer.

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
MAX_JOBS_PER_RUN       = 10   # volume cap — halved in Phase 9
MAX_SELECTED_JOBS      = 10   # raised in ADR-054 — every qualifying job reaches deep review
MAX_RESEARCH_STEPS     = 2    # ReAct loop cap
MAX_REVIEW_ROUNDS      = 3    # reflection loop cap
MAX_LLM_CALLS_PER_RUN  = 200  # global budget — raised in ADR-054
```

**Why it matters:** A single misconfigured or adversarial run cannot spend unbounded API budget. Cost is first-class, not an afterthought.

**References:** ADR-041 · ADR-052 · ADR-054

---

### 11. Conditional Execution

**v1:** All scored jobs were shown in the terminal. Tailoring was a separate command. No conditional routing.

**v2:** Execution is gated at multiple points:
- Deep review runs **only for shortlisted jobs** (≤ 3, user-selected)
- Interview Coach runs **only when match_score ≥ 75** or user requests it
- Tailoring runs **only on user request**
- Fidelity Reviewer runs **only after tailoring**

**Why it matters:** The most expensive agents (Sonnet for Critic, Coach, Tailoring) only run when there is a clear signal of value. Without conditional execution, a 10-job run would invoke all 8 agents for all 10 jobs.

**Reference:** ADR-012 · ADR-014

---

### 12. Agent Specialization

**v1:** 3 general-purpose agents. `TailoringAgent` did both generation and output formatting. `ProfileAgent` handled both PDF parsing and caching.

**v2:** 8 specialized agents. Each has one responsibility, one prompt file, one output schema, and one pattern.

| Agent | Single Responsibility |
|---|---|
| Research Agent | Company and role context extraction |
| Scoring Agent | Multi-track fitness scoring |
| Resume Critic | Gap identification and improvement suggestions |
| Review Auditor | Quality evaluation of the critic's output |
| Career Advisor | Cross-job career positioning synthesis |
| Interview Coach | Role-specific interview preparation |
| Tailoring Agent | Evidence-bound resume section rewriting |
| Fidelity Reviewer | Claim validation against original resume |

**Why it matters:** Smaller prompts improve output quality. Single-responsibility agents are easier to test, tune, and replace independently. A failure in the Fidelity Reviewer does not affect the Scoring Agent.

---

### 13. Evidence-Bound Generation

**v1:** `TailoringAgent` produced rewritten resume sections freely. No constraint on what Claude could claim. Gaps were listed but there was no mechanism to prevent fabrication.

**v2:** Every tailored claim in `TailoredResumeDraft` must include a `supporting_evidence` field referencing the exact text in the original resume that supports the claim. The Fidelity Reviewer validates every claim against this evidence before the draft is persisted.

```
Tailoring Agent: claim + supporting_evidence (from original resume)
  → Fidelity Reviewer: verifies each claim against its evidence
    → pass: draft is shown to user
    → fail: flagged claims surfaced at HITL checkpoint
```

Missing experience is labeled as a gap — never rewritten as if it exists.

**Why it matters:** Resume fabrication is a real risk in any AI tailoring system. Evidence binding makes fabrication structurally impossible within the prompt contract, and the fidelity guardrail makes it observable and stoppable even if the tailoring agent drifts.

**References:** ADR-015 · ADR-016 · ADR-017

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
v2: one cached prompt per agent per 5-minute window (all 8 agents)
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

**References:** ADR-049 · ADR-039

---

### 19. Live/Mock Mode Gate

**v1:** Not present. All runs used real API keys; the test suite was structured to avoid agent calls.

**v2:** At startup, `app/api/dependencies.py` checks `ANTHROPIC_API_KEY`:
- Present → `_build_real_deps()`: real `ClaudeProvider`, `SqliteSaver`, real scrapers
- Absent → `_build_mocked_deps()`: all 8 agents mocked, `MemorySaver`

The graph topology is identical in both modes. The entire 389-test suite runs in mock mode — no API keys required for CI.

**Why it matters:** Makes the system testable without API credentials and enables engineers to develop the UI and API layer without incurring LLM costs.

**Reference:** ADR-048

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
- **3 general agents → 8 specialized agents**: one responsibility per agent enables targeted improvement
- **Free-form generation → evidence-bound generation**: ethical AI requires structural constraints, not just ethical prompts
- **Trust then verify → verify always**: the Fidelity Reviewer runs unconditionally after every tailoring call

### What Was Avoided (and Why)

| Avoided | Reason |
|---|---|
| Global ReAct | Unpredictable cost and latency at scale; single-shot structured output is sufficient for all non-research tasks |
| Fully autonomous agents | Users must own career decisions; the system informs, it does not decide |
| Dynamic planning (LLM-chosen next step) | Adds failure modes without proportional benefit when the plan is known in advance |
| Multi-agent protocol (A2A messaging) | Premature complexity for a single-user sequential workflow; orchestrator-mediated is sufficient (ADR-009) |

---

## Future Evolution

Items still ahead, in order of value:

| Pattern | Trigger to introduce |
|---|---|
| Memory-driven personalization | After observability is mature and run history data is sufficient to validate suggestions |
| Adaptive workflow routing | After the static plan has proven stable across 50+ real runs |
| Parallel deep review | After concurrent scoring proves thread-safety in production |
| Multi-provider support | When an alternative model meaningfully outperforms Claude on a specific agent task |

Introduce each only after: observability is mature, evaluation framework is established, and the existing workflow is stable.
