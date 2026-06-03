# Observability Model – jobsearchagent-v2

---

## 1. Purpose

This document defines the observability model for **jobsearchagent-v2**.

The system includes:

* automated job discovery
* multiple agents
* LLM calls
* ReAct research steps
* reflection loops
* human-in-the-loop pauses
* report generation
* security and fidelity checks

Because of this, observability is mandatory.

The goal is to answer:

```text
What happened?
Where is the workflow now?
Which agent ran?
What did it cost?
Why did it stop, continue, or pause?
What did the user decide?
Was anything unsafe or unsupported detected?
```

---

## 2. Observability Principles

1. Every workflow has a correlation ID.
2. Every agent execution is logged.
3. Every LLM call is tracked.
4. Every meaningful state transition is recorded.
5. Every HITL decision is persisted.
6. Every reflection round is traceable.
7. Every security or fidelity issue is logged.
8. Logs should contain summaries, not sensitive raw data.
9. Observability should support debugging, UI transparency, and cost control.

---

## 3. Correlation ID

Every workflow run must have a unique `workflow_id`.

The `workflow_id` must be passed through:

* workflow services
* agents
* tools
* LLM calls
* repositories
* observability events
* reports

Example:

```text
workflow_id = wf_20260101_001
```

This allows all activity to be traced back to a single run.

---

## 4. Observability Layers

The system observes execution at six levels:

| Layer             | Purpose                                         |
| ----------------- | ----------------------------------------------- |
| Workflow          | Tracks run lifecycle and state                  |
| Agent             | Tracks agent execution                          |
| LLM               | Tracks model usage, tokens, cost, latency       |
| Tool              | Tracks deterministic tool/service activity      |
| HITL              | Tracks user decisions                           |
| Security/Fidelity | Tracks safety, privacy, and hallucination risks |

---

## 5. Workflow Observability

### Purpose

Track the lifecycle of each workflow.

### Captured Data

```text
workflow_id
workflow_type
status
current_step
started_at
updated_at
completed_at
error_message
```

### Example Statuses

```text
initialized
running
waiting_for_user
completed
failed
cancelled
```

### Example Steps

```text
job_discovery
resume_profile_loading
scoring
awaiting_job_selection
research
resume_critique
review_audit
career_advice
tailoring
fidelity_review
report_generation
```

### Storage

Primary table:

```text
workflow_runs
```

---

## 6. State Transition Observability

Every meaningful state transition should be logged.

Example:

```text
scoring → awaiting_job_selection
```

Should create an event:

```json
{
  "workflow_id": "wf_123",
  "from_step": "scoring",
  "to_step": "awaiting_job_selection",
  "reason": "batch scoring completed",
  "timestamp": "2026-01-01T10:00:00Z"
}
```

### Why It Matters

State transitions explain the workflow timeline.

They help debug:

* stuck workflows
* unexpected branches
* skipped steps
* failed recovery paths

---

## 7. Agent Observability

Every agent execution should emit events.

### Captured Data

```text
workflow_id
agent_name
event_type
input_summary
output_summary
status
duration_ms
prompt_version
model_provider
model_name
error_message
```

### Event Types

```text
started
completed
failed
retry
skipped
```

### Example

```json
{
  "workflow_id": "wf_123",
  "agent_name": "resume_critic",
  "event_type": "completed",
  "input_summary": "resume_profile + selected_job + research_context",
  "output_summary": "identified 6 resume gaps and 2 possible career gaps",
  "duration_ms": 8420
}
```

### Storage

Primary table:

```text
agent_events
```

---

## 8. LLM Call Observability

Every LLM call should be tracked separately from agent events.

### Captured Data

```text
workflow_id
agent_name
provider
model
prompt_version
schema_version
tokens_input
tokens_output
estimated_cost
latency_ms
status
error_message
created_at
```

### Why Separate LLM Calls from Agent Events?

One agent execution may involve:

* one LLM call
* multiple retries
* schema repair calls
* fallback calls

Separating LLM call records makes cost and performance visible.

### Storage

Primary table:

```text
llm_calls
```

---

## 9. Tool Observability

Tools and deterministic services should also be observable.

### Captured Data

```text
workflow_id
tool_name
event_type
input_summary
output_summary
status
duration_ms
error_message
```

### Examples

```text
job_discovery.started
job_discovery.completed
resume_parser.completed
skill_normalizer.completed
report_generator.completed
```

### Storage

Tool events can be stored in:

```text
agent_events
```

or a future dedicated table:

```text
tool_events
```

For now, storing them as event records is acceptable.

---

## 10. ReAct Research Observability

The Research Agent uses bounded ReAct.

Do not store raw hidden reasoning.

Store summaries only.

### Captured Data Per Step

```text
step_number
reasoning_summary
tool_name
tool_input_summary
observation_summary
stop_reason
```

### Example

```json
{
  "step_number": 1,
  "reasoning_summary": "Needed additional role context beyond the job description.",
  "tool_name": "fetch_job_page",
  "observation_summary": "Role emphasizes cloud modernization and enterprise platform ownership.",
  "stop_reason": null
}
```

### Stop Reasons

```text
max_research_steps_reached
enough_context_collected
tool_failed
unsafe_content_detected
```

---

## 11. Reflection Loop Observability

The Resume Critic / Review Auditor loop must be fully traceable.

Each round should store:

```text
round_number
critic_output_summary
audit_score
auditor_confidence
audit_feedback_summary
stop_reason
created_at
```

### Example Timeline

```text
Round 1: audit score 74 → continue
Round 2: audit score 83 → continue
Round 3: audit score 88 → stop
```

### Storage

Primary table:

```text
review_rounds
```

### Why It Matters

This proves whether reflection improved the output.

---

## 12. HITL Observability

Every human decision must be persisted.

### Captured Data

```text
workflow_id
decision_type
decision_value
decision_payload_json
created_at
```

### Example Decision Types

```text
select_jobs_for_deep_review
approve_tailoring
reject_tailoring
request_interview_prep
mark_job_applied
cancel_workflow
```

### Storage

Primary table:

```text
human_decisions
```

### Why It Matters

This separates:

```text
system decisions
```

from:

```text
user decisions
```

---

## 13. Security and Fidelity Observability

Security and fidelity events must be logged.

### Captured Data

```text
workflow_id
event_type
severity
description
payload_summary
created_at
```

### Event Types

```text
prompt_injection_warning
pii_redacted
tool_access_blocked
schema_validation_failed
unsupported_claim_detected
fabricated_metric_detected
cost_limit_exceeded
fidelity_review_failed
```

### Severity Levels

```text
info
warning
error
critical
```

### Storage

Primary table:

```text
security_events
```

---

## 14. Cost and Performance Observability

Track cost and performance at both call and workflow level.

### Per LLM Call

Stored in:

```text
llm_calls
```

Fields:

```text
tokens_input
tokens_output
estimated_cost
latency_ms
provider
model
```

### Per Workflow

Stored in:

```text
run_metrics
```

Fields:

```text
total_llm_calls
total_tokens_input
total_tokens_output
total_cost
total_duration_ms
```

### Per Profile (ADR-062)

Cost is attributable per profile. `llm_calls` carries no `user_id`; ownership is
the `user_id` of the `workflow_runs` row its `workflow_run_id` points at. The Cost
Dashboard scopes its aggregates to the active profile by joining through
`workflow_runs` (orphan calls with no run row COALESCE to the default profile
`"0"`), with a toggle to view system-wide spend across every profile.

---

## 15. Quality Metrics

Observability should track quality, not just execution.

### Examples

```text
overall_match_score
audit_score
auditor_confidence
review_rounds_completed
number_of_resume_gaps
number_of_career_gaps
number_of_unsupported_claims
fidelity_status
```

### Why It Matters

The system should help answer:

```text
Is the analysis getting better?
Are reflection loops improving output?
Are tailoring suggestions safe?
```

---

## 16. User-Facing Timeline

The UI should eventually show a simple timeline.

Example:

```text
✅ Job discovery completed
✅ Resume profile loaded
✅ 18 jobs scored
⏸ Waiting for job selection
✅ Research completed
✅ Resume Critic round 1 completed
✅ Auditor score: 78
✅ Resume Critic round 2 completed
✅ Auditor score: 88
✅ Career advice generated
✅ Report generated
```

This makes the system understandable and trustworthy.

---

## 17. Error Observability

Failures should be logged clearly.

### Captured Data

```text
workflow_id
step
component
error_type
message
recoverable
suggested_action
created_at
```

### Example

```json
{
  "workflow_id": "wf_123",
  "step": "job_discovery",
  "component": "linkedin_scraper",
  "error_type": "blocked_page",
  "message": "Job page blocked automated access.",
  "recoverable": true,
  "suggested_action": "Use pasted job description fallback."
}
```

---

## 18. What Not to Log

Do not log:

* API keys
* secrets
* raw full resumes unless explicitly needed and protected
* raw hidden reasoning
* unnecessary PII
* unredacted prompts containing sensitive personal data

Prefer logging:

* summaries
* IDs
* counts
* status
* structured outputs
* error categories

---

## 19. Observability Service

Create a service:

```text
app/services/observability_service.py
```

Actual surface (as implemented — this section previously listed an aspirational
set; corrected here to the real API, ADR-074 minor):

```python
# Agent lifecycle (one started -> completed|failed per agent call)
log_agent_started(...) -> event_id
log_agent_completed(...)
log_agent_failed(...)
# LLM calls
log_llm_call(...)
# Step transitions (node-level; wired ADR-074 Gap 2)
log_step_started(...) -> step_execution_id
log_step_completed(...)
log_step_failed(...)
# Human decisions (wired ADR-074 Gap 1)
log_human_decision(...)
# Security events (wired ADR-073)
log_security_event(...)
# Run metrics
init_run_metrics(...)
finalize_run_metrics(...)
get_llm_calls_by_run(...)
compute_run_totals_from_llm_calls(...)
```

Module-level helpers in the same file, for call sites that have no injected
service instance (all never-crash):

```python
emit_security_event_safe(...)        # ADR-073 (config/kickoff cost-cap)
record_api_request_safe(...)         # ADR-074 Gap 5 (HTTP middleware)
log_artifact_decision(...)           # ADR-074 Gap 1 (decision endpoints)
fidelity_review_security_description(...)  # PII-safe description builder
```

There is no `start_workflow` / `update_workflow_status` / `log_state_transition`
/ `log_tool_event` / `log_review_round` / `log_error` / `complete_workflow` /
`fail_workflow` method — those were proposed in the original design but never
built. Workflow lifecycle is handled by the `register_run` / `generate_report`
nodes writing `workflow_runs` directly; review rounds persist via
`ReviewRepository`. The workflow orchestrator calls this service consistently.

> **`log_security_event` is wired (ADR-073).** Five deterministic emit sites
> (SSRF block, PII redaction, Fidelity reject/unsupported claim, cost-cap
> violation, and a runtime budget-cap trip in `score_jobs`/`deep_review` per
> ADR-076) now write `security_events`, and they are visualized system-level on
> the **System Dashboard** (renamed from Cost Dashboard) alongside Performance,
> Reliability, Scalability, and Cost. Run-less events use the `SYSTEM_RUN_ID`
> sentinel; reads scope by profile (ADR-062) via
> `SecurityRepository.list_for_user` + `app/services/system_health.py`.
> Descriptions are PII-safe by construction (counts/field-names/reason-classes
> only). See `security_observability_design.md`.

---

## 20. Database Tables

Core observability tables:

```text
workflow_runs
agent_events
llm_calls
review_rounds
human_decisions
run_metrics
security_events
step_executions   (node-level timing; wired ADR-074 Gap 2)
api_requests      (HTTP-layer requests; wired ADR-074 Gap 5)
```

> **ADR-074 wired three previously-unobserved layers:** `human_decisions`
> (the approve/revise/reject/edit audit, Gap 1), `step_executions` (node-level
> timing, Gap 2), and `api_requests` (HTTP request method/route-template/status/
> latency via a FastAPI middleware, Gap 5 — the API surface was entirely
> unobserved before). All are profile-scoped on the System Dashboard. Still open:
> out-of-graph `run_metrics` (Gap 3) and the thread-local `last_call_usage` race
> (Gap 4). See ADR-074.

> **ADR-077 closed the failure-path cost hole:** `log_llm_call` previously ran on
> the success path only, so a billed-but-unparseable response (schema repair
> exhausted) wrote no `llm_calls` row and every cost rollup undercounted it. The
> provider now attaches the billed `LLMUsage` to `LLMProviderError`, `BaseAgent`'s
> failure path logs it (the failure stays marked in `agent_events`), and a repaired
> call sums both billed attempts. A completeness invariant test
> (`test_cost_logging_completeness.py`) guards that every LLM-call site logs cost.

Optional future table:

```text
tool_events
state_transitions
```

For the current SQLite-based system, these can initially be represented through existing event tables if needed.

---

## 21. Observability and Testing

Tests should verify that key workflow steps emit events.

Example tests:

```text
scoring workflow creates workflow_run
scoring agent logs agent_event
LLM call logs provider/model/tokens
review loop creates review_rounds
HITL pause creates pending decision
security violation creates security_event
```

---

## 22. Anti-Patterns to Avoid

Avoid:

* only logging final results
* hiding agent failures
* storing raw chain-of-thought
* mixing workflow state with event history
* skipping cost tracking
* skipping prompt version tracking
* failing silently
* letting UI state be the only place where progress exists

---

## 23. Final Principle

Observability is not just logging.

It is how the system earns trust.

The system must be able to explain:

```text
what ran
why it ran
what it produced
what it cost
what the user decided
what risks were detected
```

If the system cannot answer those questions, it is not observable enough.
