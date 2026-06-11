# Agent graph — top-level overview

A bird's-eye view of every agent v2 calls into, grouped by responsibility,
plus the in-graph workflow flow that wires the funnel agents together and
the out-of-graph operations that drive everything else.

For the per-agent input / output contract, constraints, and observability
events, see [`agent_model.md`](agent_model.md). For the workflow node graph
in detail, see [`workflow_model.md`](workflow_model.md).

## All agents at a glance

Thirteen LLM-using components (eleven `BaseAgent` subclasses + two utility helpers),
grouped by responsibility.

![Agent graph — LLM-using components grouped by responsibility](images/agent_graph.png)

> The PNG above is rendered deterministically from
> `tools/figure_renderer/specs/agent_graph.json` (the same HTML/CSS engine as the
> article figures) — the JSON spec is the render source of truth; the Mermaid block
> below is a textual mirror kept in sync. Re-render after any agent change with
> `python tools/render_figures.py agent_graph`, then copy
> `blogs/blog_images/agent_graph.png` over `docs/architecture/images/agent_graph.png`.

```mermaid
flowchart TB
    subgraph Funnel [Job-search funnel - in-graph LangGraph]
        a0[RelevanceFilterAgent - Structured output, batched - opt-in pre-scoring]
        a1[ResearchAgent - Bounded ReAct]
        a2[ScoringAgent - Structured output]
        a3[ResumeCritic - Critique]
        a4[ReviewAuditor - Evaluator and reflection]
        a5[CareerAdvisor - Advisory]
        a6[InterviewCoach - on-demand by default, ADR-085]
    end

    subgraph Tailoring [On-demand tailoring - out-of-graph]
        a7[TailoringAgent - Evidence-bound generation]
    end

    subgraph Clinic [Resume Clinic - out-of-graph]
        a8[ResumeReviewerAgent - Structured output, job-agnostic]
        a9[ResumeChatAgent - Iterative revision per turn]
    end

    subgraph Guardrail [Validation - runs after every generation agent]
        a10[FidelityReviewer]
    end

    subgraph Helpers [Utility LLM helpers - not BaseAgent]
        a11[ResumeParser uses resume_parser]
        a12[CustomUrlExtractor uses custom_url_extractor]
    end
```

## Reference table

All values from `config/config.example.yaml` (per-agent provider + model
assignment) and `tests/model_pins.json` (build-time pin).

### Funnel agents — run inside the LangGraph workflow

| Agent | `AGENT_NAME` | Pattern | When it runs |
|---|---|---|---|
| `RelevanceFilterAgent` | `relevance_filter` | Structured output (batch) | Opt-in (`search.relevance_filter`, ADR-079). One cheap call before scoring; hard-drops seniority/relevance mismatches. |
| `ResearchAgent` | `research_agent` | Bounded ReAct | Always — once per discovered job, before scoring. |
| `ScoringAgent` | `scoring_agent` | Structured output (batch) | Always — scores every researched job. |
| `ResumeCritic` | `resume_critic` | Critique | Only for jobs that pass the deep-review gate. |
| `ReviewAuditor` | `review_auditor` | Evaluator / reflection | Always paired with `ResumeCritic` in a bounded loop (`MAX_REVIEW_ROUNDS=2`). |
| `CareerAdvisor` | `career_advisor` | Advisory | After the reflection loop stops. |
| `InterviewCoach` | `interview_coach` | On-demand (ADR-085) | On-demand by default via `POST /workflows/{wf}/jobs/{job}/interview-prep`. The in-graph coach auto-fires only when `scoring.auto_interview_prep` is on (default off) and a selected job clears `min_match_score`. |

### Out-of-graph operations — driven by REST endpoints

| Agent | `AGENT_NAME` | Pattern | Triggered by |
|---|---|---|---|
| `TailoringAgent` | `tailoring_agent` | Evidence-bound generation | `POST /workflows/{wf}/jobs/{job}/tailorings` (ADR-055). Always followed by `FidelityReviewer`. |
| `ResumeReviewerAgent` | `resume_reviewer` | Structured output (job-agnostic) | `POST /users/{id}/resume-clinic` (ADR-066). Always followed by `FidelityReviewer` on the `rewrites`. |
| `ResumeChatAgent` | `resume_chat` | Iterative revision | `POST /resume-clinic/{id}/chat` (ADR-068), one call per chat turn. Always followed by `FidelityReviewer`. |

### Guardrail

| Agent | `AGENT_NAME` | Pattern | When it runs |
|---|---|---|---|
| `FidelityReviewer` | `fidelity_reviewer` | Validation | After every generation agent's output — `TailoringAgent`, `ResumeReviewerAgent.rewrites`, `ResumeChatAgent.overhaul.rewrites`. **A human `edit` decision is owner-authored and NOT re-reviewed (ADR-059).** |

### Utility helpers — not `BaseAgent` subclasses

| Component | `AGENT_NAME` (for cost/pin) | Purpose | Where it runs |
|---|---|---|---|
| `ResumeParser` | `resume_parser` | Enhance the heuristic PDF parse with an LLM pass to fill structured fields (`gpa`, `honors[]`, `skill_groups[]`, …) | `app/services/resume_parser.py`. Invoked on every resume upload that's not in the SHA-256 cache. |
| `CustomUrlScraper` | `custom_url_extractor` | LLM-fallback extraction when heuristics (JSON-LD, OpenGraph, article tag) fail to pull the job-posting fields out of a URL | `app/services/custom_url_scraper.py`. Per workflow run, only for URLs in `state["custom_urls"]`. |

## The in-graph workflow flow

The funnel agents are wired into a single LangGraph state machine defined in
`app/workflows/workflow_graph.py`. Conditional entry routes a fresh run to
`register_run`; ADR-060 phase-2 (manual scoring continuation) re-enters at
`score_jobs` on the same thread.

![The in-graph workflow as swimlanes: a Discover lane (register_run, discover_jobs, load_resume); the scoring_mode_gate routing to manual await_scoring_selection, the opt-in relevance_filter, or score_jobs; a Score lane (relevance_filter, score_jobs, await_job_selection); the deep_review_gate; a Deep review and advise lane (deep_review with the critic-auditor reflection loop, career_advice, interview_router); the interview_router gate; and a Finish lane (interview_prep, generate_report, END). Stateful LangGraph, no interrupt.](images/workflow_node_graph.png)

<sub>Figure source: `tools/figure_renderer/specs/workflow_node_graph.json`.</sub>

Per-node agent calls:

| Node | Agents invoked | Notes |
|---|---|---|
| `register_run` | (none) | Writes the initial state to `workflow_runs` so the UI sees it. |
| `discover_jobs` | (none) | Uses `JobDiscoveryService` + the scrapers (Adzuna, LinkedIn, CustomUrl, and the ADR-081 ATS-direct Greenhouse/Lever). `CustomUrlScraper` may call the `custom_url_extractor` LLM as a fallback per URL. |
| `load_resume` | (none) | Reads the active resume from `ResumeRepository`. |
| `await_scoring_selection` | (none) | ADR-060: parks until `POST /workflows/{wf}/scoring` continues. |
| `relevance_filter` | `RelevanceFilterAgent` | ADR-079, opt-in: one batched call drops seniority/relevance mismatches before scoring. Keep-all on failure. |
| `score_jobs` | `ResearchAgent` ⇒ `ScoringAgent` | Per-job: research first (bounded ReAct), then structured-output scoring. |
| `await_job_selection` | (none) | Auto-selects up to `MAX_SELECTED_JOBS` qualifying jobs (no interrupt). |
| `deep_review` | `ResumeCritic` ⇄ `ReviewAuditor` | Reflection loop bounded by `MAX_REVIEW_ROUNDS` + the auditor's `stop` signal. |
| `career_advice` | `CareerAdvisor` | One call per selected job. |
| `interview_prep` | `InterviewCoach` | On-demand by default (ADR-085): auto-fires only when `scoring.auto_interview_prep` is on (or `user_requested_interview_prep`). |
| `generate_report` | (none) | Aggregates everything written by the earlier nodes; terminal status. |

## The two out-of-graph operations

Tailoring and the Resume Clinic both follow the **ADR-055 pattern**: the
endpoint loads state from the checkpointer (or the resume row), calls agents
directly, and persists via repos. No `interrupt()`, no LangGraph entry, no
HITL graph pauses.

### On-demand tailoring

![On-demand tailoring flow: a completed workflow, then POST tailorings runs the Tailoring Agent and Fidelity Reviewer to a tailored_resumes draft, then POST tailorings decisions records approve, revise, reject or edit.](images/api_ondemand_tailoring.png)

<sub>Figure source: `tools/figure_renderer/specs/api_ondemand_tailoring.json` (shared with the API reference).</sub>

### Resume Clinic + chat-revise

![Resume Clinic flow: POST resume-clinic runs the Resume Reviewer and Fidelity Reviewer into a resume_clinic_reviews row; a chat-revise loop (POST chat) runs the Resume Chat Agent and Fidelity Reviewer to update edited_json each turn; then decisions or discard-edits; then GET export in any format via a deterministic renderer with no agent call.](images/resume_clinic_flow.png)

<sub>Figure source: `tools/figure_renderer/specs/resume_clinic_flow.json`.</sub>

## Cross-cutting invariants

These apply to every agent and are enforced in code, not just docs:

- **One interface** — every agent calls the model through `LLMClient`. No
  agent imports `ClaudeProvider` or `OpenAIProvider` directly.
- **Structured output is the contract** — every call returns a validated
  Pydantic schema (`min_length`, `Literal` enums, etc.) before anything
  downstream trusts it.
- **Guardrails baked in every prompt** — `app/prompts/shared/guardrails.txt`
  is auto-injected by `PromptLoader` for every agent.
- **Pinned per-agent assignment** — `tests/model_pins.json` records the
  `(provider, model)` each agent was last validated against. A
  configuration swap fails the build invariant test until the pin is
  updated alongside.
- **Bounded execution** — every agent has explicit step / round / call
  limits (`MAX_RESEARCH_STEPS`, `MAX_REVIEW_ROUNDS`,
  `MAX_LLM_CALLS_PER_JOB`, `MAX_LLM_CALLS_PER_RUN`).
- **Observability invariant** — every call writes an `llm_calls` row tagged
  with the run's `workflow_run_id`, so the per-profile Cost Dashboard
  attributes spend correctly (clinic + chat turns flow under the
  lightweight `workflow_type="resume_clinic"` row).
- **Fidelity always runs on agent-authored rewrites** — never on a human
  `edit` decision; the reviewer polices the agent, not the accountable
  human (ADR-059).

## References

- [`agent_model.md`](agent_model.md) — full per-agent contract.
- [`workflow_model.md`](workflow_model.md) — the LangGraph workflow in
  detail (nodes, edges, routers, conditional logic).
- [`api_surface_overview.md`](api_surface_overview.md) — companion diagram
  for the REST surface.
- [ADR-055](adr/ADR-055-on-demand-tailoring-as-out-of-graph-operation.md) —
  the out-of-graph pattern Tailoring and the Resume Clinic both follow.
- [ADR-059](adr/ADR-059-retire-in-graph-hitl-and-add-human-edit-decision.md) —
  human-as-final-author; Fidelity polices the agent, not the human.
- [ADR-066](adr/ADR-066-standalone-resume-clinic.md) — the standalone Resume
  Clinic.
- [ADR-068](adr/ADR-068-chat-revise-loop-for-the-resume-clinic.md) — the
  chat-revise loop on top of the Clinic.
