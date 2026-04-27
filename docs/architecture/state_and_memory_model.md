# State and Memory Model – jobsearchagent-v2

---

## 1. Purpose

This document defines how **workflow state** and **memory** work in `jobsearchagent-v2`.

This is one of the most important architecture documents because state and memory directly affect:

* workflow orchestration
* agent inputs
* agent outputs
* persistence
* observability
* debugging
* personalization
* safety

The goal is to prevent hidden context, uncontrolled memory use, and state drift.

---

## 2. Core Distinction

`jobsearchagent-v2` uses two different concepts:

| Concept        | Purpose                             | Scope                | Lifetime                |
| -------------- | ----------------------------------- | -------------------- | ----------------------- |
| Workflow State | Tracks the current execution        | One workflow run     | Temporary but persisted |
| Memory         | Stores learned preferences/patterns | Across workflow runs | Long-term               |
| Database       | Stores durable records              | Application-wide     | Persistent              |
| Prompt Context | Selected data sent to LLM           | One agent call       | Ephemeral               |

These must not be confused.

---

## 3. Design Principle

The system follows this rule:

> State is the source of truth for the current workflow. Memory is optional context, not authority.

Agents must not maintain hidden internal memory.

Agents receive:

```text
selected workflow state
+ relevant memory
+ task prompt
+ output schema
```

Agents return:

```text
structured output
```

The orchestrator validates that output and updates state.

---

## 4. Workflow State

Workflow state is the **short-term working memory** for one workflow run.

It answers:

```text
What does this workflow know right now?
What has already happened?
What is waiting to happen next?
Why did the system stop, continue, or pause?
```

---

## 5. Workflow State Ownership

The workflow orchestrator owns state.

Agents may read selected state and return structured outputs, but they do not directly mutate state.

The update pattern is:

```text
Orchestrator loads state
        ↓
Orchestrator builds agent input
        ↓
Agent returns structured output
        ↓
Orchestrator validates output
        ↓
Orchestrator writes approved update to state
        ↓
State is persisted
```

This prevents agents from creating uncontrolled state changes.

---

## 6. Proposed WorkflowState Shape

The initial `WorkflowState` should include:

```python
class WorkflowState:
    workflow_id: str
    workflow_type: str
    status: str
    current_step: str

    user_id: str | None

    resume_id: str | None
    resume_profile: dict | None
    resume_version: int | None

    search_criteria: dict
    raw_jobs: list[dict]
    normalized_jobs: list[dict]
    scored_jobs: list[dict]
    selected_jobs: list[dict]

    research_context: dict
    skill_gaps: dict

    review_rounds: list[dict]
    final_resume_review: dict | None

    career_advice: dict | None
    interview_prep: dict | None
    tailored_resume: dict | None
    fidelity_review: dict | None

    pending_decision: dict | None
    human_decisions: list[dict]

    report: dict | None

    run_metrics: dict
    errors: list[dict]

    created_at: str
    updated_at: str 
    effective_config: dict
```

---

## 7. Workflow Status Values

Recommended status values:

| Status           | Meaning                           |
| ---------------- | --------------------------------- |
| initialized      | Workflow created                  |
| running          | Workflow actively executing       |
| waiting_for_user | Workflow paused for HITL          |
| completed        | Workflow completed successfully   |
| failed           | Workflow failed                   |
| cancelled        | User or system cancelled workflow |

---

## 8. Workflow Step Values

Recommended `current_step` values:

```text
initialized
job_discovery
resume_profile_loading
scoring
awaiting_job_selection
research
resume_critique
review_audit
reflection_decision
career_advice
interview_prep
tailoring
fidelity_review
awaiting_user_approval
report_generation
completed
failed
```

These values should be stable and used consistently in UI, logs, and persistence.

---

## 9. State Sections

### 9.1 Workflow Metadata

Tracks the workflow identity and lifecycle.

```json
{
  "workflow_id": "wf_123",
  "workflow_type": "full_career_review",
  "status": "running",
  "current_step": "scoring"
}
```

---

### 9.2 Resume State

Stores the selected resume/profile for the run.

```json
{
  "resume_id": "res_001",
  "resume_version": 3,
  "resume_profile": {
    "roles": [],
    "skills": [],
    "experience": [],
    "leadership_scope": [],
    "architecture_scope": []
  }
}
```

Notes:

* Raw resume text should not be passed to every agent by default.
* Prefer parsed/redacted profile.
* Store raw text only when needed for parsing or report generation.

---

### 9.3 Job State

Tracks discovered, normalized, scored, and selected jobs.

```json
{
  "raw_jobs": [],
  "normalized_jobs": [],
  "scored_jobs": [],
  "selected_jobs": []
}
```

Definitions:

| Field           | Purpose                             |
| --------------- | ----------------------------------- |
| raw_jobs        | Original results from scrapers/APIs |
| normalized_jobs | Jobs converted to common schema     |
| scored_jobs     | Jobs with JobScore outputs          |
| selected_jobs   | Jobs chosen for deep review         |

---

### 9.4 Research State

Stores bounded ReAct research output.

```json
{
  "research_context": {
    "company_summary": "",
    "role_context": "",
    "technology_signals": [],
    "leadership_signals": [],
    "domain_signals": [],
    "risk_flags": [],
    "research_steps": []
  }
}
```

Research steps should store summaries only, not hidden chain-of-thought.

---

### 9.5 Review State

Stores reflection loop progress.

```json
{
  "review_rounds": [
    {
      "round_number": 1,
      "critic_output": {},
      "audit_output": {},
      "audit_score": 78,
      "auditor_confidence": 82,
      "stop_reason": null
    }
  ],
  "final_resume_review": {}
}
```

This allows the system to answer:

```text
Did the critique improve across rounds?
Why did the loop stop?
What changed between rounds?
```

---

### 9.6 Career Intelligence State

Stores higher-level guidance.

```json
{
  "career_advice": {},
  "interview_prep": {},
  "tailored_resume": {},
  "fidelity_review": {}
}
```

These are produced after the deep review workflow.

---

### 9.7 HITL State

Stores pending and completed human decisions.

```json
{
  "pending_decision": {
    "decision_type": "select_jobs_for_deep_review",
    "message": "Select jobs for deep review.",
    "options": ["approve", "reject", "defer"],
    "payload": {}
  },
  "human_decisions": []
}
```

The backend owns workflow routing.

The user owns business decisions.

---

### 9.8 Metrics State

Stores run-level metrics.

```json
{
  "run_metrics": {
    "llm_calls": 0,
    "tokens_input": 0,
    "tokens_output": 0,
    "estimated_cost": 0.0,
    "latency_ms": 0
  }
}
```

---

### 9.9 Error State

Stores recoverable and non-recoverable errors.

```json
{
  "errors": [
    {
      "step": "job_discovery",
      "error_type": "scraper_blocked",
      "message": "Job page blocked automated access.",
      "recoverable": true,
      "suggested_action": "Use pasted job description fallback."
    }
  ]
}
```

## 9.10 Effective Configuration

Workflow state includes the effective configuration used during execution.

```json
{
  "effective_config": {
    "search": {
      "roles": [],
      "locations": [],
      "max_jobs": 20
    },
    "scoring": {
      "weights": {}
    }
  }
}

---

## 10. State Persistence

Workflow state should be persisted in SQLite.

Recommended table:

```sql
CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    state_json TEXT,
    resume_id TEXT,
    job_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);
```

The `state_json` column stores the current state snapshot.

Additional tables store important detailed outputs separately:

```text
review_rounds
resume_reviews
career_advice
interview_prep
tailored_resumes
human_decisions
agent_events
llm_calls
run_metrics
security_events
reports
```

---

## 11. State Snapshot vs Event History

The system should use both:

| Storage Type | Purpose                           |
| ------------ | --------------------------------- |
| state_json   | Current snapshot of workflow      |
| event tables | Historical trace of what happened |

Example:

```text
state_json tells where the workflow is now.
agent_events and review_rounds tell how it got there.
```

Both are needed.

---

## 12. State Update Rules

All state updates must follow these rules:

1. Only the orchestrator updates state.
2. Agents return structured outputs, not direct state mutations.
3. Every meaningful state transition is logged.
4. State must validate against schema before persistence.
5. Unknown fields should be rejected or explicitly handled.
6. State updates should be idempotent where possible.
7. Each update should include `updated_at`.

---

## 13. Memory Model

Memory is long-term learning across workflow runs.

It should help the system personalize future scoring, advice, and recommendations.

Memory is not free-form chat history.

Memory must be structured.

---

## 14. What Memory Stores

Memory may store:

| Memory Type              | Example                                            |
| ------------------------ | -------------------------------------------------- |
| preferred_role           | Principal Architect, Director Engineering          |
| rejected_job_pattern     | Pure IC roles without architecture influence       |
| preferred_industry       | Utilities, energy, enterprise SaaS                 |
| company_preference       | Companies to prefer or avoid                       |
| successful_resume_signal | Cloud modernization, global team leadership        |
| interview_feedback       | Weakness in explaining AI/ML architecture          |
| scoring_calibration      | User values architecture ownership more than title |
| tailoring_preference     | Conservative tailoring, no aggressive rewrites     |

---

## 15. Memory Table

Recommended table:

```sql
CREATE TABLE memory_items (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    memory_key TEXT,
    memory_value_json TEXT NOT NULL,
    confidence INTEGER,
    source_workflow_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

## 16. Memory Retrieval

Memory should be retrieved selectively.

Do not send all memory to every agent.

Recommended retrieval pattern:

```text
Agent about to run
        ↓
Orchestrator checks which memory types are relevant
        ↓
Retrieve only relevant memory
        ↓
Inject memory summary into prompt
```

Examples:

| Agent           | Relevant Memory                                              |
| --------------- | ------------------------------------------------------------ |
| Scoring Agent   | role preferences, rejected job patterns, scoring calibration |
| Career Advisor  | career goals, preferred positioning, prior advice            |
| Interview Coach | prior interview feedback, weak areas                         |
| Tailoring Agent | tailoring preferences, unsupported claim sensitivity         |

---

## 17. Memory Write Rules

Memory should not be written automatically from every LLM output.

Memory writes should be controlled.

Recommended rules:

1. Memory writes should come from explicit user feedback or repeated patterns.
2. Memory should include confidence.
3. Memory should reference the source workflow run.
4. Sensitive memory should require user approval.
5. Memory should be editable or deletable.

---

## 18. Memory vs Current Evidence

Memory must never override current resume/job evidence.

Example:

```text
Memory says user prefers cloud architecture roles.
Current job is a pure data analyst role.
System may use memory to lower recommendation priority,
but cannot fabricate cloud architecture fit.
```

Memory influences interpretation.

It does not create facts.

---

## 19. Prompt Context Construction

Before calling an agent, the orchestrator builds prompt context from:

```text
shared ethics guardrails
agent-specific instructions
selected workflow state
relevant memory
output schema
```

Prompt context must exclude:

```text
unnecessary PII
raw secrets
irrelevant memory
hidden internal reasoning
untrusted content as instructions
```

---

## 20. Agent State Access

Agents should receive only the state they need.

Examples:

### Scoring Agent receives:

```text
resume_profile
normalized_job
research_context
relevant role preference memory
```

### Resume Critic receives:

```text
resume_profile
selected_job
research_context
score_result
skill_gaps
prior audit feedback
```

### Review Auditor receives:

```text
latest_resume_review
resume_profile
selected_job
ethics_guardrails
prior rounds
```

### Career Advisor receives:

```text
final_resume_review
resume_profile
selected_job
score_result
relevant career memory
```

### Tailoring Agent receives:

```text
resume_sections
selected_job
final_resume_review
career_advice
tailoring_constraints
```

---

## 21. State and Observability

Every state transition should create an observable event.

Example:

```text
current_step: scoring → awaiting_job_selection
```

Should log:

```text
workflow_id
from_step
to_step
timestamp
reason
```

This gives the UI and developer a trustworthy execution timeline.

---

## 22. HITL and State

When the system needs user input, state should move to:

```text
status = waiting_for_user
```

And include:

```json
{
  "pending_decision": {
    "decision_type": "approve_tailoring",
    "message": "Approve this tailored resume draft?",
    "options": ["approve", "edit", "reject"],
    "payload": {}
  }
}
```

When the user responds:

1. Persist the human decision.
2. Clear `pending_decision`.
3. Update workflow status.
4. Resume workflow.

---

## 23. State and Error Recovery

State should support recovery after failures.

Examples:

| Failure                   | State Behavior                         |
| ------------------------- | -------------------------------------- |
| Job scraper fails         | record error, allow pasted JD fallback |
| LLM schema failure        | retry, then mark recoverable error     |
| Reflection loop stagnates | stop loop and use best review          |
| User cancels              | mark workflow cancelled                |
| Tailoring fails fidelity  | return to revision or reject output    |

---

## 24. State and Bounded Execution

State must track execution counts.

Examples:

```json
{
  "research_step_count": 2,
  "review_round_count": 3,
  "llm_call_count": 7
}
```

This enables enforcement of:

```text
MAX_RESEARCH_STEPS
MAX_REVIEW_ROUNDS
MAX_LLM_CALLS_PER_JOB
MAX_COST_PER_RUN
```

---

## 25. Recommended Code Location

Workflow state schema:

```text
app/state/workflow_state.py
```

Pydantic schemas:

```text
app/schemas/
```

Memory service:

```text
app/memory/memory_service.py
```

Repository:

```text
app/repositories/workflow_repository.py
app/repositories/memory_repository.py
```

---

## 26. Implementation Guidance

Start with:

1. `WorkflowState`
2. `WorkflowStatus`
3. `WorkflowStep`
4. `HumanDecision`
5. `RunMetrics`
6. `WorkflowError`

Then add specialized outputs:

1. `JobScore`
2. `ResearchContext`
3. `ResumeReview`
4. `ReviewAudit`
5. `CareerAdvice`
6. `InterviewPrep`
7. `TailoredResumeDraft`
8. `FidelityReview`

Do not build memory behavior before workflow state is stable.

---

## 27. Anti-Patterns to Avoid

Avoid:

* agents directly mutating state
* agents storing their own memory
* sending all memory to all agents
* using memory as factual evidence
* storing hidden reasoning as memory
* letting UI session state become workflow state
* using raw unvalidated dicts everywhere
* allowing arbitrary new state fields
* mixing runtime state and long-term memory

---

## 28. Final Principle

State is how the system knows what is happening now.

Memory is how the system learns over time.

The orchestrator controls both.

Agents reason over selected context, return structured outputs, and never become the source of truth.
