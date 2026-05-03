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

## Article 9 — Multi-Provider Abstraction: ModelRegistry and Per-Agent Assignment

**Headline:**
*How to keep your agent code from getting locked to one LLM vendor — and the tradeoff most production teams should not pay*

**Hook:**
Per-agent provider and model selection sounds like an obvious win until you have run two providers in production for a month. Each one has its own rate limits, retry semantics, output quirks, observability dashboard, and on-call surface. v2 supports it because I wanted to learn the pattern. Here is the pattern, the seam that makes it work, and the honest tradeoff that should shape your default.

**Core content:**
- The seam: agents depend only on `LLMClient`; ModelRegistry resolves `(provider, model)` per agent and caches one instance per pair (ADR-053). What this lets you change without touching agent code.
- Per-agent assignment in `app/providers/model_registry.py::DEFAULT_AGENT_ASSIGNMENT` and the user override path via `agents.{name}.{provider, model}` in `user_config`. Restart-to-apply.
- The two providers: `ClaudeProvider` and `OpenAIProvider` — same `LLMClient` interface, same retry policy (6 attempts, jittered exponential backoff capped at 60s; 429s honor `retry-after` capped at 90s), same `complete_with_usage(...) -> (dict, LLMUsage)` typed return.
- Where the cost ducks: the prompt cache key is per-provider; switching providers mid-run costs cache hits.
- The honest tradeoff: doubled rate-limit profiles, doubled retry tuning, doubled failure-mode triage. When per-agent assignment pays back; when it doesn't.

**Length:** 1300–1500 words
**New patterns introduced:** Provider-Indirect Agent (LLMClient seam), Registry-Resolved Per-Agent Assignment

---

## Article 10 — Heuristics-First, LLM-Fallback for Untrusted External Content

**Headline:**
*The pattern that turned my custom-URL job scraper into something I trust — without paying for an LLM call on every URL*

**Hook:**
Users want to paste career-page URLs from anywhere on the web and have the agent process them. That is an open door for cost blow-ups and prompt injection. v2's CustomUrlScraper closes the door with a four-step pipeline that only reaches for the LLM when the heuristics genuinely cannot handle the page.

**Core content:**
- The pipeline: JSON-LD JobPosting → OpenGraph metadata → article tag → LLM fallback (sonnet) → log-and-skip with the URL recorded in workflow `errors[]`.
- Why the order matters: structured signals are cheaper, more reliable, and not vulnerable to prompt injection. Falling back to LLM only when heuristics fail keeps the cost curve flat for the typical case.
- 25-URL hard cap, 30s fetch timeout per URL — never raise without reviewing cost impact.
- The trust boundary: the system prompt declares custom-URL content as untrusted external data; the same XML-tag isolation pattern from Article 2's Pattern 13 applies.
- Generalisable: the same four-step structure works for any agent that ingests arbitrary user-supplied URLs, PDFs, or documents.

**Length:** 1200–1400 words
**New patterns introduced:** Heuristics-First / LLM-Fallback Cascade, Structured-Signal Preference

---

## Article 11 — Hybrid Configuration: YAML Defaults Plus DB User Overrides with Safe-Key Allowlists

**Headline:**
*Letting users tune your agent without letting them break it: the configuration model behind v2*

**Hook:**
Once your agent has more than a handful of users, every one of them wants to change something — the threshold, the model, the search criteria, the retention window. Some of those changes are safe; some are catastrophic. The hybrid configuration pattern in v2 (ADR-046) makes the boundary explicit and enforces it at the read path, not the write path.

**Core content:**
- The two layers: YAML defaults in `config/config.yaml` (gitignored, copied from `config.example.yaml`) and per-user overrides in the `user_config` SQLite table.
- The protected-key allowlist: hard limits, retention windows, prompt definitions are read-only via UI. Users can override `tailoring.style`, `scoring.min_match_score`, `agents.{name}.{provider, model}` and a few others. The Settings UI surfaces only the safe keys.
- The merge order: YAML → user override → effective config; cap-and-clamp at the read path so a stale user override against a tightened limit does not leak past the cap.
- Why ConfigService is read at run start, not at every node: snapshotting `effective_config` into `WorkflowState` keeps a run's behavior stable even if the config changes mid-run.
- Generalisable: the same allowlist pattern fits any LLM app that lets end users tune behavior without exposing the controls that should never be turned.

**Length:** 1300–1500 words
**New patterns introduced:** Hybrid Configuration with Safe-Key Allowlist, Effective-Config Snapshotting

---

## Article 12 — Testing a Multi-Agent System

**Headline:**
*How I keep 8 agents and a stateful workflow correct without spending a cent on LLM calls in CI*

**Hook:**
Most agent test suites either run real LLM calls (slow, expensive, flaky) or mock everything so completely that the test exercises nothing useful. v2's test strategy is neither — and at 456 tests, 1 skipped, the suite catches real regressions on every commit.

**Core content:**
- The mock-by-API-key-absence pattern (ADR-048): if `ANTHROPIC_API_KEY` is missing, the dependency wiring builds mocked agents with deterministic side effects. Every test runs in mock mode by default.
- The `@pytest.mark.integration` marker for live-API smoke tests; gated, opt-in, never on CI's hot path.
- Pydantic schemas as the agent contract: every agent's output is validated at the boundary, so mock and real outputs are interchangeable for downstream nodes.
- The concurrency invariant test for `score_jobs` and `deep_review`: 5 jobs × 100ms agent calls must complete in <300ms, locking in the ThreadPoolExecutor speedup so a future refactor cannot silently revert it.
- The repair pattern: how I caught seven phase validation notebooks that had drifted with the codebase, and what the recovery loop looked like (static drift scan → mock-mode execution → cell-level repair → CHANGELOG entry).

**Length:** 1300–1500 words
**New patterns introduced:** Mock-by-API-Key-Absence, Concurrency Invariant Test, Schema-as-Contract Test

---

## Article 13 — The Strangler Fig: Keeping v1 Productive While Building v2

**Headline:**
*Why I did not delete v1 — and how the wrapper pattern let v2 borrow what worked*

**Hook:**
The textbook advice for a v2 rewrite is "strangle the legacy and delete it." The textbook is sometimes wrong. v1 of this agent is still in the repo — not as dead code, but as borrowed components — and v2 is better for it.

**Core content:**
- The decision (ADR-001 + ADR-044): keep v1 stable, build v2 alongside; reuse what is genuinely valuable, replace what is structurally wrong.
- What got reused: v1 Adzuna scraper wrapped by v2's `ConcurrentAdzunaScraper` (ADR-050), `EXCLUDED_TITLE_KEYWORDS` and `TECH_DESCRIPTION_KEYWORDS` filter lists in `models/filters.py`. Why wrapping beat re-implementing.
- What got replaced: orchestration (script → graph), agent contract (free-form → Pydantic), HITL (post-hoc curation only → curation + auto-gate + out-of-graph operations), persistence (single SQLite → run-aware schema + SqliteSaver checkpoints).
- The hard rule: do not modify v1 files. v1 is a known-stable reference; v2 wraps or replaces.
- The deletion question: when do you actually delete v1? The honest answer for a learning project: never, until the wrapper layer is fully replaced.

**Length:** 1200–1400 words
**New patterns introduced:** Wrapper-Adapter for Legacy Components, ADR-Anchored v1/v2 Boundary

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

## Suggested publishing order

No fixed cadence. Each article needs original thought, fresh diagrams, and a real reader takeaway. Posting twice a week is possible; doing each article justice is more important than hitting a cadence target.

| Order | Article | Notes |
|---|---|---|
| 1 | Article 3 — Architecture Shift / Methodology overview | Series launch. |
| 2 | Article 4 — 8 Specialized Agents | Foundation for the rest. |
| 3 | Article 9 — Multi-Provider Abstraction | Naturally follows Article 4's per-agent model assignment. |
| 4 | Article 5 — Stateful Orchestration | LangGraph + SqliteSaver + interrupt-resume. |
| 5 | Article 6 — HITL Evolution | Builds on Article 5's interrupt-resume primitive. |
| 6 | Article 7 — Bounded Reflection | Pairs with Article 10 (heuristics-first/LLM-fallback) — both are about bounded LLM use. |
| 7 | Article 10 — Heuristics-First, LLM-Fallback | Cost + safety pattern for untrusted external content. |
| 8 | Article 12 — Testing a Multi-Agent System | The article every reader silently wants once they have read 4–7. |
| 9 | Article 8 — Cost Architecture at Scale | Concurrency, model tiering, volume cap. |
| 10 | Article 11 — Hybrid Configuration | Safe-key allowlist; YAML + DB overrides. |
| 11 | Article 13 — Strangler Fig Migration | Closing piece on the v1/v2 boundary discipline. |
