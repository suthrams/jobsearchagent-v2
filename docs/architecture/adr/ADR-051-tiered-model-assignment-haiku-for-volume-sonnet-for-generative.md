# ADR-051: Tiered Model Assignment — Haiku for Volume/Validation, Sonnet for Generative

## Status
Accepted

## Context
Phase 9 cost analysis found that using Sonnet for all agents was the dominant cost driver. Sonnet is 12× more expensive than Haiku per token. The Research Agent alone (one Sonnet call per job × 10 jobs = 10 Sonnet calls per run) accounted for 60–70% of total cost.

Not all agents have the same quality requirements:
- High-volume agents (run every job) and validation agents (checking, not generating) tolerate Haiku quality
- Generative and advisory agents (produce prose the user reads and acts on) require Sonnet quality

## Decision
Assign each agent to the lowest-cost model that meets its quality requirement:

| Agent | Model | Rationale |
|---|---|---|
| Research Agent | Haiku | Summarization and signal extraction — not deep reasoning |
| Scoring Agent | Haiku | Structured classification — already Haiku |
| Review Auditor | Haiku | Quality-checking existing text — validation task |
| Fidelity Reviewer | Haiku | Binary claim verification — validation task |
| Resume Critic | Sonnet | Deep gap analysis — quality-sensitive |
| Career Advisor | Sonnet | Generative advisory prose — quality-sensitive |
| Interview Coach | Sonnet | Generative coaching content — quality-sensitive |
| Tailoring Agent | Sonnet | Evidence-bound resume generation — quality-sensitive |

## Rationale
- 4 agents moved to Haiku (Research, Auditor, Fidelity) without quality regression risk
- Scoring was already Haiku — no change
- Sonnet retained for agents where the user directly reads and acts on the output
- ADR-027 mandates cost tracking — this decision is directly measurable via the `llm_calls` table

## Consequences

### Positive
- ~75–85% cost reduction per typical run
- Model assignment is explicit in `app/api/dependencies.py` with inline rationale comments
- Easy to revert individual agent assignments if quality regression is observed

### Tradeoffs
- Research Agent on Haiku may produce shallower company summaries than Sonnet
- Tradeoff is acceptable given MAX_RESEARCH_STEPS = 2 limits the depth of the ReAct loop regardless of model

## Implementation Notes
- `app/api/dependencies.py` — two `ClaudeProvider` instances (`sonnet_provider`, `haiku_provider`) injected per agent
- Prompt caching (`cache_control: ephemeral` in `PromptLoader`) remains active for both providers
