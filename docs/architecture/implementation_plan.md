# Implementation Plan – jobsearchagent-v2

---

## Overview

This document defines the phased implementation approach for jobsearchagent-v2.

The strategy is **bottom-up, layer by layer**. Each phase builds on a validated foundation before intelligence is added. No phase begins until the previous phase has passing tests and a completed review gate.

The guiding constraint from ADR-034:

> Do not build the full platform before proving the core workflow.

---

## Implementation Strategy

```text
Phase 1: Foundation        → schemas, state, DB, config
Phase 2: Services          → deterministic tools (no LLM)
Phase 3: LLM Provider      → abstraction + prompt assembly
Phase 4: Agents            → one at a time, scoring first
Phase 5: Orchestrator      → wire agents into workflows
Phase 6: UI                → thin layer on existing Streamlit
```

---

## Phase 1 — Foundation

### Goal

Establish the data contracts and persistence layer that all other phases depend on.

### Deliverables

**Workflow State**

- `WorkflowState`
- `WorkflowStatus`
- `WorkflowStep`
- `HumanDecision`
- `RunMetrics`
- `WorkflowError`

**Agent Output Schemas**

- `JobScore`
- `ResearchContext`
- `ResumeReview`
- `ReviewAudit`
- `CareerAdvice`
- `InterviewPrep`
- `TailoredResumeDraft`
- `FidelityReview`

**SQLite Tables (17)**

```text
workflow_runs · jobs · resumes · job_scores
review_rounds · resume_reviews · career_advice · interview_prep
tailored_resumes · reports · human_decisions · user_config
agent_events · llm_calls · run_metrics · security_events · memory_items
```

**Config**

- `config.yaml` with system defaults
- `ConfigService` — merges YAML defaults with DB user overrides

**Code Locations**

```text
app/state/workflow_state.py
app/schemas/
app/repositories/
app/services/config_service.py
```

### Tests

- Schema validation passes with valid inputs
- Schema validation rejects missing required fields
- Schema validation rejects invalid types and ranges
- DB tables created correctly
- ConfigService merges YAML and DB overrides in correct priority order
- ConfigService enforces system limits (user cannot override LLM model, safety limits, cost limits)

### Review Gate 1

> Inspect all schemas and DB table definitions before any logic is written.
> Confirm data contracts are complete and match the agent model and data model documents.

---

## Phase 2 — Services (No LLM)

### Goal

Implement all deterministic services. No LLM calls in this phase.

### Deliverables

| Service | Responsibility |
| --- | --- |
| `JobDiscoveryService` | Abstracts scrapers, normalizes jobs, deduplicates |
| `ResumeParser` | Parses uploaded resume into structured profile |
| `SkillNormalizer` | Normalizes skill names against `data/skills.yaml` |
| `StatusManager` | Deterministic, non-AI workflow/job status updates |
| `ObservabilityService` | Logs all events, transitions, LLM calls, decisions |
| `ReportGenerator` | Assembles and formats final reports (Markdown, DOCX, PDF) |

**Migration from v1**

- v1 scrapers (LinkedIn, Adzuna, Ladders) are preserved behind `JobDiscoveryService`
- v1 resume parsing logic is wrapped and normalized
- v1 filters (`EXCLUDED_TITLE_KEYWORDS`, `TECH_DESCRIPTION_KEYWORDS`) remain in `models/filters.py`

**Code Locations**

```text
app/services/job_discovery_service.py
app/services/resume_parser.py
app/services/skill_normalizer.py
app/services/status_manager.py
app/services/observability_service.py
app/services/report_generator.py
scrapers/          ← preserved from v1, wrapped by JobDiscoveryService
```

### Tests

- `JobDiscoveryService` returns normalized `JobPosting` schema
- Deduplication removes duplicate job IDs
- `ResumeParser` returns valid `ResumeProfile` schema
- `SkillNormalizer` maps known aliases to canonical skill names
- `StatusManager` rejects invalid status transitions
- `ObservabilityService` persists correct event types to correct tables
- `ReportGenerator` produces valid Markdown output from fixture workflow state

### Review Gate 2

> Inspect each service and its unit tests.
> Confirm all deterministic logic is correct and fully tested before any agent depends on it.

---

## Phase 3 — LLM Provider Layer

### Goal

Establish the provider abstraction and prompt system that all agents will use.

### Deliverables

**Provider Abstraction**

- `LLMClient` — abstract interface
- `ClaudeProvider` — Anthropic SDK, preserving v1 call patterns
- `OpenAIProvider` — stub for future use

**Prompt System**

- `app/prompts/shared/guardrails.txt` — shared ethics + injection defense
- `app/prompts/agents/` — one prompt file per agent
- Prompt loader + assembly (guardrails + role + task + constraints + schema)
- Prompt versioning (`agent_name:v1`)

**Code Locations**

```text
app/providers/llm_client.py
app/providers/claude_provider.py
app/providers/openai_provider.py
app/prompts/shared/guardrails.txt
app/prompts/agents/
```

### Tests

- Prompt assembly includes guardrails for every agent
- Prompt injection defense string is present in all prompts
- `ClaudeProvider` returns structured output matching requested schema
- Retry logic fires once on LLM failure
- Schema repair fires once on validation failure
- Prompt version is logged on every call

### Review Gate 3

> Inspect the provider abstraction and the prompt structure.
> Confirm the guardrails template and prompt assembly pattern before it is replicated across all 8 agents.

---

## Phase 4 — Agents

### Goal

Implement all 8 agents. One at a time. Each agent is complete (prompt, schema, implementation, tests) before the next begins.

### Agent Delivery Order

```text
1. Scoring Agent          ← first pattern to validate
2. Research Agent         ← bounded ReAct
3. Resume Critic          ← critique pattern
4. Review Auditor         ← evaluator/reflection
5. Career Advisor         ← advisory reasoning
6. Interview Coach        ← conditional execution
7. Tailoring Agent        ← evidence-bound generation
8. Fidelity Reviewer      ← validation/guardrail
```

### Per-Agent Delivery Checklist

For each agent:

- [ ] Prompt file written (`app/prompts/agents/<agent>.txt`)
- [ ] Input schema defined
- [ ] Output schema defined
- [ ] Agent class implemented
- [ ] Observability events emitted
- [ ] Security constraints enforced
- [ ] Unit tests pass
- [ ] Integration test with fixture LLM response passes

### Agent Constraints Summary

| Agent | Pattern | Tools | Max Steps |
| --- | --- | --- | --- |
| Scoring Agent | Structured reasoning | None | — |
| Research Agent | Bounded ReAct | job fetcher, extractor | 2 |
| Resume Critic | Critique | None | — |
| Review Auditor | Evaluator | None | — |
| Career Advisor | Advisory | None | — |
| Interview Coach | Conditional | None | — |
| Tailoring Agent | Evidence-bound | None | — |
| Fidelity Reviewer | Guardrail | None | — |

**Code Locations**

```text
app/agents/scoring_agent.py
app/agents/research_agent.py
app/agents/resume_critic.py
app/agents/review_auditor.py
app/agents/career_advisor.py
app/agents/interview_coach.py
app/agents/tailoring_agent.py
app/agents/fidelity_reviewer.py
```

### Tests

- Output matches defined schema
- Guardrails present in prompt
- Prompt injection content in input is ignored in output
- No fabricated fields when evidence is absent
- Observability events emitted on start/complete/fail
- Research Agent stops at `MAX_RESEARCH_STEPS = 2`
- Fidelity Reviewer flags unsupported claim fixtures

### Review Gate 4a — After Scoring Agent

> Inspect the Scoring Agent end-to-end: prompt file, schema, agent class, and tests.
> Confirm the agent pattern is correct before it is replicated across the remaining 7 agents.

### Review Gate 4b — After All Agents

> Inspect the complete agent layer.
> Confirm each agent is consistent in structure, constraints, and observability.

---

## Phase 5 — Workflow Orchestrator

### Goal

Wire agents, services, and state into complete, runnable workflows.

### Deliverables

**Workflows**

| Workflow | Key Steps |
| --- | --- |
| Job Discovery | search → fetch → normalize → deduplicate → persist |
| Resume Profile | load or parse → PII minimize → persist |
| Scoring | batch score jobs → rank → persist |
| Shortlist + HITL | pause → present ranked jobs → resume on selection |
| Deep Review | research → critic → auditor loop → career advisor |
| Interview Prep | conditional on score or user request |
| Tailoring | tailor → fidelity review → HITL approval |
| Reporting | aggregate → format → persist → return |
| Error Handling | retry → fallback → fail safely |

**Execution Limits Enforced**

```text
MAX_JOBS_PER_RUN = 20
MAX_SELECTED_JOBS = 3
MAX_RESEARCH_STEPS = 2
MAX_REVIEW_ROUNDS = 3
MAX_LLM_CALLS_PER_JOB = 10
MAX_LLM_CALLS_PER_RUN = 50
```

**HITL State Transitions**

```text
running → waiting_for_user → running → completed
```

**Code Locations**

```text
app/workflows/job_discovery_workflow.py
app/workflows/resume_profile_workflow.py
app/workflows/scoring_workflow.py
app/workflows/deep_review_workflow.py
app/workflows/tailoring_workflow.py
app/workflows/reporting_workflow.py
app/workflows/orchestrator.py
```

### Tests

- Workflow state transitions are correct at each step
- Reflection loop stops at `audit_score >= threshold` or `MAX_REVIEW_ROUNDS`
- Stagnation detection stops the loop
- HITL pause creates `pending_decision` in state
- HITL resume clears `pending_decision` and continues correctly
- Execution limits are enforced and logged
- Failed LLM call triggers retry then graceful error state
- All workflow steps emit observability events

### Review Gate 5

> Inspect the orchestrator wiring a complete Deep Review workflow end-to-end using fixture data.
> Confirm state transitions, HITL pause/resume, and reflection loop behavior before UI is added.

---

## Phase 6 — UI

### Goal

Connect the Streamlit UI to v2 workflow services. Preserve v1 structure where possible.

### Strategy

```text
Reuse UI → Replace backend calls → Extend interaction model
```

- v1 screens are preserved and refactored to call v2 workflow services
- New screens are added for Deep Review, HITL, Observability, and Run History
- UI does not orchestrate agents or maintain hidden workflow state

### Screens

```text
start.py           ← resume select + search criteria + run
settings.py        ← user preferences (bounded)
jobs.py            ← ranked job list + selection
deep_review.py     ← analysis output per job
interview_prep.py  ← interview preparation
tailoring.py       ← resume suggestions + approval
reports.py         ← final report + export
run_history.py     ← past workflow runs
observability.py   ← execution timeline + cost + events
```

**Code Locations**

```text
app/ui/streamlit_app.py
app/ui/pages/
```

### Tests

- UI renders correct decision options from backend `pending_decision`
- UI submits structured decision payloads to backend
- UI displays correct status per workflow state value
- UI does not call LLM providers directly

### Review Gate 6

> Walk through the complete user journey end-to-end with fixture data.
> Confirm the UI reflects backend state correctly and all HITL interactions work.

---

## Testing Strategy Summary

| Layer | Test Type | LLM Calls |
| --- | --- | --- |
| Schemas | Unit | None |
| Services | Unit | None |
| LLM Provider | Unit (mocked) | None |
| Agents | Integration (fixture responses) | None in CI |
| Workflows | Integration (fixture state) | None in CI |
| Full flow | E2E (fixture) | None in CI |
| Prompt quality | Manual eval runs | Real calls |

**Key rule:** No real LLM calls in CI. Agents are tested with recorded fixture responses. Real LLM calls are reserved for manual evaluation runs tracked against prompt versions (ADR-043).

---

## Review Gate Summary

| Gate | Phase | What You Review |
| --- | --- | --- |
| Gate 1 | Foundation | Schemas + DB tables — the data contracts |
| Gate 2 | Services | Deterministic services + unit tests |
| Gate 3 | LLM Provider | Provider abstraction + prompt structure |
| Gate 4a | First Agent | Scoring Agent — the agent pattern |
| Gate 4b | All Agents | Complete agent layer consistency |
| Gate 5 | Orchestrator | Full workflow wiring with fixture data |
| Gate 6 | UI | Complete end-to-end user journey |

---

## Migration from v1

Per ADR-044, migration is feature-by-feature:

| v1 Component | v2 Destination |
| --- | --- |
| `scrapers/` | Preserved, wrapped by `JobDiscoveryService` |
| `agents/scoring_agent.py` | Replaced by v2 Scoring Agent with structured schema |
| `agents/tailoring_agent.py` | Replaced by v2 Tailoring Agent + Fidelity Reviewer |
| `storage/` | Replaced by v2 repositories and 17-table schema |
| `dashboard.py` | Replaced by v2 Streamlit pages |
| `claude/` | Replaced by v2 LLM provider abstraction |
| `prompts/` | Replaced by v2 prompt system with shared guardrails |

v1 remains stable and runnable throughout. v2 is developed in parallel.

---

## Start Point

Begin with **Phase 1 — Foundation**.

First deliverable: `WorkflowState` and the agent output schemas in `app/schemas/`.

These are the contracts everything else builds on, carry zero LLM risk, and catching misalignments here prevents compounding errors in later phases.
