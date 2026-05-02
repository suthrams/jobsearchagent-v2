# LinkedIn Article Series — Job Search Agent v2

## Series framing

The first two articles covered building and running v1:
- **Article 1** (published): 8 patterns from building the agent
- **Article 2** (published/draft): 7 more patterns from running it in production

This v2 series picks up where they left off. The thesis:

> Running v1 for months taught me what a working prototype cannot do.
> v2 is the answer to that question — a ground-up refactor into a real multi-agent system.
> This series is what that architecture actually looks like and what it cost to build it.

The series is for software engineers and architects who have watched agentic AI demos and want to understand what production multi-agent systems involve — the design decisions, the tradeoffs, and the things the courses skip.

---

## Article 3 — The Architecture Shift

**Headline:**
*I rebuilt my job search agent from scratch. Here is what a production multi-agent system actually looks like.*

**Hook:**
v1 worked. I ran it for months. It taught me 15 patterns. Then I hit the ceiling — the things a smart sequential script simply cannot do regardless of how well you tune it.

**Core content:**
- The specific v1 limitations that triggered the rewrite (not a vague desire to refactor — concrete problems)
- Architecture diagram: v1 (main.py → 3 agents → SQLite → Streamlit) vs v2 (LangGraph → 8 agents → FastAPI/Streamlit split → SqliteSaver)
- The one insight that changed everything: only the orchestrator updates state; agents return structured outputs and never touch the database or filesystem directly
- Four things the redesign forced me to think about differently: state, specialization, human decision-making, cost at scale
- Series roadmap: what each article covers and why in that order

**Length:** 900–1100 words (overview, not deep dive — sets up the series)
**New patterns introduced:** Stateful Graph Orchestration (introduced, not deep-dived)

---

## Article 4 — Designing 8 Specialized Agents

**Headline:**
*Why I went from 3 agents to 8 — and the principle behind every cut*

**Hook:**
The first question every engineer asks when they see "8 agents" is: why not just use one? It is a fair question. Here is the specific reasoning behind every decomposition decision.

**Core content:**
- The decomposition principle: one agent per reasoning mode, not one per feature
- The 6 agent patterns used: Bounded ReAct, Structured Output, Critique, Evaluator/Reflection, Advisory, Evidence-bound generation, Validation/Guardrail
- Per-agent table: agent → pattern → model → trigger condition
- Model tiering as a design-time decision, not an afterthought: Haiku for high-volume/validation, Sonnet for generative/advisory — with the actual assignment rationale per agent
- The agent contract: structured Pydantic output in, nothing written directly out — why this constraint matters for the orchestrator
- Code comparison: ResearchAgent (ReAct with bounded tool calls) vs ScoringAgent (structured output, no reasoning loop)

**Length:** 1400–1700 words
**New patterns introduced:** Bounded ReAct, Evidence-Bound Generation, Agent Contract (Pydantic output only)

---

## Article 5 — Stateful Workflow Orchestration

**Headline:**
*Why I stopped using Python loops to control my agent and what I got instead*

**Hook:**
Every time v1 ran, it started from scratch. No memory of previous steps. No way to pause mid-run and resume. No way to recover from a crash without re-running everything. That is not a workflow — it is a script.

**Core content:**
- What LangGraph gives you that sequential code cannot: durable state, graph-structured control flow, checkpoint persistence, resumability
- SqliteSaver: how workflow state survives crashes and process restarts
- The interrupt/resume pattern: how the graph pauses waiting for a human decision and resumes from the exact same checkpoint — with the state serialization that makes this possible
- WorkflowState schema: what lives in state, why it is the single source of truth, why agents never write to it directly
- The workflow graph walkthrough (discover → research → score → HITL → deep_review → career_advice → report)

**Length:** 1400–1600 words
**New patterns introduced:** Stateful Graph Orchestration, Checkpoint Persistence, Interrupt-Resume

---

## Article 6 — The Evolution of Human-in-the-Loop

**Headline:**
*HITL, v1 vs v2: from "click to exclude" to mid-workflow checkpoints — and why both are right*

**Hook:**
In Article 2 I described the curation pattern: human exclusion after results are produced, improving signal quality over time. v2 adds a different kind of HITL entirely — one where the human's decision determines what the system does next. They are not the same pattern and they are not interchangeable.

**Core content:**
- v1 HITL: post-hoc curation — human acts after results, no impact on workflow path
- v2 HITL: mid-workflow interrupt — human acts at the branch point that determines downstream execution
- The FastAPI/Streamlit split: why writes go through the API (validated, audited, resumable) while reads go direct from SQLite (fast, available during a run)
- Decision validation before graph resumption: why the backend validates job IDs before injecting them into the graph
- The tailoring approval flow: approve / request revision / reject as a three-way branch with bounded revision rounds
- When to use each variant: curation for continuous quality improvement, interrupt for branching decisions with downstream cost consequences

**Length:** 1300–1500 words
**New patterns introduced:** Interrupt-Resume Checkpoint, Decision Validation Gate, Write-via-API / Read-direct split

---

## Article 7 — Bounded Reflection Loops

**Headline:**
*Teaching a multi-agent system to improve its own output — and knowing when to stop*

**Hook:**
The first time I ran a reflection loop without a stop condition it was still running 20 minutes later. Every iteration looked like meaningful improvement. None of it was. That is the failure mode that bounded reflection is designed to prevent.

**Core content:**
- The Resume Critic → Review Auditor → improve loop: what each agent contributes and why you need both
- Two independent stop conditions: quality threshold (audit score ≥ 75) and iteration cap (MAX_REVIEW_ROUNDS = 3)
- Stagnation detection: if improvement between rounds < 5 points, stop regardless of score
- Why stagnation matters more than the cap: most loops that waste compute are stagnating, not simply running long
- The Fidelity Reviewer as a guardrail, not a quality agent: it runs after tailoring to verify claims against the original resume — the evidence-bound generation pattern enforced at runtime
- Cost curve: what a 3-round reflection loop costs vs. a 1-round pass, and when the extra cost is worth it

**Length:** 1300–1500 words
**New patterns introduced:** Bounded Reflection, Stagnation Detection, Runtime Fidelity Guardrail

---

## Article 8 — Cost Architecture at Scale

**Headline:**
*Running 8 agents on every job search: the cost model that makes it viable*

**Hook:**
My first v2 run with all agents active cost more in one pass than my entire v1 weekly average. The architecture was right but the cost model was not. Here is how I fixed it — and what the real levers are when you are running 8 agents at per-job volume.

**Core content:**
- The problem: v1 had 3 operations with model routing; v2 has 8 agents, each making multiple calls, on every job
- The concurrent scoring breakthrough: ThreadPoolExecutor dropped scoring time from 75s to 20s at no additional cost
- The Phase 9 model tiering table: which agents moved from Sonnet to Haiku, and the explicit reasoning for each decision
- The volume lever: MAX_JOBS_PER_RUN=10 vs 20 — halving discovery + research call volume is often more impactful than model selection
- Cost scenario table: discovery-only (~$0.02) → full run with deep review (~$0.15) → with tailoring (~$0.25)
- The compound effect: tiering + concurrency + volume cap together achieved 75–85% cost reduction per run
- Where the remaining cost is and where it should be: the deep review pass is intentionally expensive because that is where the value is

**Length:** 1400–1600 words
**New patterns introduced:** Concurrent Agent Execution, Agent-Level Model Tiering, Volume Cap as Cost Control

---

## Publishing notes

- Each article should stand alone but reward the reader who followed from Article 1
- Lead every article with a failure or a specific limitation — not a feature
- Keep the v1 callbacks explicit: readers who followed the series need the thread
- One primary Mermaid diagram per article; second diagram only if the concept genuinely needs it
- Code comparisons use BEFORE/AFTER format consistent with Articles 1 and 2
- Further reading section at the end of each article linking to authoritative sources
- Hashtags: #AgenticAI #AIEngineering #LangGraph #Anthropic #MultiAgentSystems #SoftwareArchitecture #Claude

---

## Suggested publishing cadence

| Week | Article |
|---|---|
| Week 1 | Article 3 — Architecture Shift (overview, series launch) |
| Week 2 | Article 4 — 8 Specialized Agents |
| Week 3 | Article 5 — Stateful Orchestration |
| Week 4 | Article 6 — HITL Evolution |
| Week 5 | Article 7 — Bounded Reflection |
| Week 6 | Article 8 — Cost Architecture |
