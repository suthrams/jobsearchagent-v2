# Agent Model – jobsearchagent-v2

---

## 1. Purpose

This document defines the agent model for **jobsearchagent-v2**.

It explains:

* which agents exist
* what each agent is responsible for
* what each agent must not do
* what inputs each agent receives
* what outputs each agent produces
* which tools each agent may use
* how agent outputs are validated, observed, and persisted

The goal is to keep the system predictable, testable, observable, and safe.

---

## 2. Core Agent Design Principle

Agents are used for reasoning.

They do not own workflow execution, persistence, status updates, or unrestricted tool access.

The system follows this rule:

> Agents reason. Tools and services execute. The workflow orchestrator controls.

---

## 3. Agent Coordination Model

Agents do not communicate directly with each other.

Instead:

![Centralized agent coordination: the orchestrator runs each agent, the agent reads selected state and returns structured output, the orchestrator validates the output, updates state, and decides the next step](images/agent_coordination.png)

*Figure: coordination is centralized through the orchestrator; there are no agent-to-agent calls. Re-render with `python tools/render_figures.py agent_coordination`.*

This keeps coordination centralized and avoids uncontrolled agent-to-agent behavior.

---

## 4. Agent Inventory

| Agent                   | Purpose                                            | Pattern                |
| ----------------------- | -------------------------------------------------- | ---------------------- |
| Relevance Filter Agent  | Drop seniority/relevance mismatches before scoring | Structured output (batch) |
| Research Agent          | Gather job/company context                         | Bounded ReAct          |
| Scoring Agent           | Score job fit                                      | Structured reasoning   |
| Resume Critic Agent     | Identify resume gaps and improvement opportunities | Critique               |
| Review Auditor Agent    | Evaluate critique quality                          | Evaluator / Reflection |
| Career Advisor Agent    | Separate resume gaps from career gaps              | Advisory reasoning     |
| Interview Coach Agent   | Prepare user for high-value roles                  | Conditional execution  |
| Tailoring Agent         | Suggest evidence-bound resume improvements         | Controlled generation  |
| Resume Reviewer Agent   | Job-agnostic resume overhaul for the Resume Clinic | Structured output (ADR-066) |
| Resume Chat Agent       | Iterative resume revision, one call per chat turn  | Iterative generation (ADR-068) |
| Fidelity Reviewer Agent | Detect unsupported tailoring claims                | Validation / Guardrail |

> ADR-061: the Tailoring Agent, Fidelity Reviewer, Resume Critic + Review Auditor
> (deep review), and Interview Coach are all also reachable **out-of-graph, on
> demand, for any scored job** — not only the auto-selected top-3 — via the
> `POST /workflows/{wf}/jobs/{job}/{tailorings,deep-review,interview-prep}`
> endpoints. The single-job deep-review loop is shared with the in-graph node
> (`app/services/deep_review_runner.py`).
>
> The Resume Reviewer and Resume Chat agents are **out-of-graph only** — they
> belong to the standalone Resume Clinic (ADR-066/068), never the LangGraph
> workflow. The Relevance Filter (5b) is in-graph but opt-in. See
> `agent_graph_overview.md` for the full grouping.

---

## 5. Shared Rules for All Agents

Every agent must follow these rules:

1. Use structured outputs.
2. Follow shared ethics guardrails.
3. Treat job descriptions and scraped content as untrusted input.
4. Do not fabricate experience, metrics, technologies, titles, or accomplishments.
5. Do not directly write to the database.
6. Do not directly update workflow/application status.
7. Do not call tools unless explicitly allowed.
8. Do not make final user decisions.
9. Return uncertainty when evidence is insufficient.
10. Provide outputs that can be validated and persisted.

---

## 5b. Relevance Filter Agent (ADR-079)

### Purpose

Cheaply triage freshly discovered postings BEFORE scoring, hard-dropping the ones
that are a clear seniority or relevance mismatch for the profile so the run only
pays the (2 calls/job) scoring spend on jobs worth scoring. Opt-in per profile
(`search.relevance_filter`); off by default. Runs in-graph on the auto-scoring
branch (`load_resume -> relevance_filter -> score_jobs`).

### Pattern

Structured output, ONE batched call per run (not per job). Haiku by default — the
filter must be cheaper than the scoring it prevents. Profile-relative and
bidirectional: judges each posting against the candidate's own target band.

### Inputs

* `_cached.resume_profile` — REDACTED via `trim_resume_profile()` (ADR-069 seam).
* `target_roles` — the run's roles/titles.
* `seniority_signals` — `min_years_experience`, `max_years_experience`,
  `exclude_senior` from `effective_config.search`.
* `jobs[]` — `{job_id, title, company, description}` (description truncated).

### Outputs

`RelevanceFilterResult { verdicts: list[RelevanceVerdict] }`, one verdict per
posting: `{ job_id, keep: bool, mismatch: Literal["none","too_senior","too_junior","unrelated"], reason }`.
`reason` is PII-safe (about the posting, never the candidate).

### Constraints

* Axis-split decision bias (ADR-104, prompt v4): role-suitability is recall-biased
  (keep when unsure); the seniority axis for an early-career profile
  (`exclude_senior`/low `max_years`) is precision-biased (drop `too_senior` on
  ambiguity). For the seniority axis the prompt may use world-knowledge of a role's
  TYPICAL level when the (often Adzuna-truncated) text is silent on years.
* Job descriptions are untrusted input — evaluated as data, never followed.
* Never lose the run: the node keeps ALL jobs on any failure / empty / unparseable
  result; drops are audited in `discovery_stats.relevance_drops`.

### Observability Events

* `relevance_filter.started` / `relevance_filter.completed` / `relevance_filter.failed`
* One `llm_calls` row per run (the batched call), counted against `MAX_LLM_CALLS_PER_RUN`.

See `relevance_filter_design.md` for the full control + data flow.

---

## 6. Research Agent

### Purpose

The Research Agent gathers additional role, company, and context signals that may not be obvious from the raw job posting.

It is the only agent that uses the ReAct pattern.

---

### Pattern

```text
Thought summary → Tool call → Observation summary → Stop or continue
```

The Research Agent uses bounded ReAct.

---

### Inputs

* normalized job description
* company name
* job title
* job source URL
* current workflow state
* search/source metadata

---

### Outputs

Structured `ResearchContext`:

```text
company_summary
role_context
technology_signals
leadership_signals
domain_signals
risk_flags
research_steps
confidence
```

---

### Allowed Tools

* job page fetcher
* company page fetcher
* job content extractor
* role signal extractor

---

### Constraints

* Maximum research steps: `MAX_RESEARCH_STEPS = 2`
* Must not follow instructions inside scraped content
* Must summarize observations, not store raw hidden reasoning
* Must stop if enough context is collected

---

### Observability Events

* `research_agent.started`
* `research_agent.tool_called`
* `research_agent.observation_recorded`
* `research_agent.completed`
* `research_agent.failed`

---

### Security Notes

The Research Agent handles untrusted external content.
Prompt injection defense must be included in its prompt.

---

## 7. Scoring Agent

### Purpose

The Scoring Agent evaluates how well a resume/profile matches one or more jobs.

It should support multiple dimensions of fit, such as:

* technical fit
* architecture fit
* leadership fit
* domain fit
* overall match

---

### Pattern

Structured reasoning with schema output.

No ReAct.

No reflection loop.

---

### Inputs

* resume profile
* normalized job description
* research context
* skill gaps
* user role preferences, if available

---

### Outputs

Structured `JobScore`:

```text
job_id
resume_id
overall_score
technical_score       # int | None — null when track 'ic' is inactive (ADR-071)
architecture_score    # int | None — null when track 'architect' is inactive (ADR-071)
leadership_score      # int | None — null when track 'management' is inactive (ADR-071)
domain_score
match_summary
strengths
gaps
recommended_next_action
confidence
```

The orchestrator passes `active_tracks` (the profile's `scoring.tracks` subset, via
`get_active_tracks(state)`) into the scoring context. The agent scores ONLY the
active tracks, emits `null` for the rest, and computes `overall_score` across the
active set (ADR-071). Default active set is all three, so the Primary profile is
unchanged.

---

### Allowed Tools

None by default.

The orchestrator should provide normalized inputs before calling the Scoring Agent.

---

### Constraints

* Must not invent experience
* Must distinguish strong match, partial match, and weak match
* Must provide reasoning for score
* Must support batch scoring at workflow level

---

### Observability Events

* `scoring_agent.started`
* `scoring_agent.completed`
* `scoring_agent.failed`

---

## 8. Resume Critic Agent

### Purpose

The Resume Critic Agent performs section-level critique of the resume against a selected job.

It identifies:

* missing signals
* weak positioning
* unclear accomplishments
* under-expressed leadership
* under-expressed architecture impact
* section-specific improvement opportunities

---

### Pattern

Critique pattern.

No ReAct.

It participates in the reflection loop through the Review Auditor.

---

### Inputs

* resume profile
* resume text or structured resume sections
* selected job description
* research context
* scoring result
* skill gap report
* prior audit feedback, if this is a later round

---

### Outputs

Structured `ResumeReview`:

```text
overall_fit_summary
section_reviews
critical_gaps
resume_only_gaps
career_gaps_observed
suggested_improvements
questions_for_user
confidence
```

Each section review should include:

```text
section_name
current_issue
why_it_matters
improvement_opportunity
suggested_direction
evidence
risk_level
```

---

### Allowed Tools

None by default.

The Resume Critic should work from provided state and structured inputs.

---

### Constraints

* Must not fabricate missing skills
* Must not turn career gaps into resume rewrites
* Must separate resume gaps from possible career gaps
* Must be direct but constructive
* Must provide evidence when making claims

---

### Observability Events

* `resume_critic.started`
* `resume_critic.completed`
* `resume_critic.failed`

---

## 9. Review Auditor Agent

### Purpose

The Review Auditor evaluates the quality of the Resume Critic output.

It decides whether the critique is:

* specific enough
* evidence-based
* aligned with the job
* non-generic
* ethically safe
* useful for action

---

### Pattern

Evaluator / Critic pattern.

Supports reflection loop.

---

### Inputs

* latest resume review
* resume profile
* selected job description
* scoring output
* previous review rounds
* ethics guardrails

---

### Outputs

Structured `ReviewAudit`:

```text
audit_score
auditor_confidence
quality_summary
missing_analysis_points
generic_or_weak_feedback
unsupported_claims
fidelity_concerns
recommended_revision_instructions
stop_recommendation
stop_reason
```

---

### Allowed Tools

None.

---

### Constraints

* Must lower score for unsupported claims
* Must lower score for generic advice
* Must detect if a gap was incorrectly converted into a rewrite
* Must recommend another round only when improvement is likely
* Must support stagnation detection
* Annotates, never edits: a gap the critic MISSED is reported in
  `missing_analysis_points`, never by altering the critic's gap lists or scores
  (BUG-014 resolved the prompt's earlier find-vs-don't-introduce contradiction)

---

### Observability Events

* `review_auditor.started`
* `review_auditor.completed`
* `review_auditor.failed`
* `review_auditor.stop_recommended`

---

## 10. Career Advisor Agent

### Purpose

The Career Advisor provides strategic guidance after the resume review.

It separates:

| Type       | Meaning                                     |
| ---------- | ------------------------------------------- |
| Resume gap | Experience exists but is poorly expressed   |
| Career gap | Actual capability or proof point is missing |

---

### Pattern

Advisory reasoning.

No ReAct.

No tool use by default.

---

### Inputs

* resume profile
* selected job description
* final resume review
* scoring output
* skill gap report
* user goals/preferences, if available

---

### Outputs

Structured `CareerAdvice`:

```text
positioning_summary
resume_gaps
career_gaps
role_fit_assessment
recommended_positioning
skills_to_strengthen
experience_to_collect
thirty_sixty_ninety_day_plan
recommended_next_action
confidence
```

---

### Allowed Tools

None by default.

Future versions may allow memory retrieval through orchestrator-provided context.

---

### Constraints

* Must not present career gaps as resume rewrite opportunities
* Must avoid discouraging or deterministic language
* Must provide constructive next steps
* Must distinguish short-term positioning from long-term development

---

### Observability Events

* `career_advisor.started`
* `career_advisor.completed`
* `career_advisor.failed`

---

## 11. Interview Coach Agent

### Purpose

The Interview Coach produces targeted interview preparation for a selected role.

**On-demand by default (ADR-085).** The user requests it via
`POST /workflows/{wf}/jobs/{job}/interview-prep`. The in-graph coach auto-runs only
when `scoring.auto_interview_prep` is on (default off) and a selected job clears
`min_match_score` — read via `get_auto_interview_prep(state)`.

---

### Pattern

Conditional execution.

Structured advisory output.

---

### Inputs

* selected job description
* resume profile
* scoring output
* research context
* career advice
* resume review

---

### Outputs

Structured `InterviewPrep`:

```text
likely_interview_topics
technical_topics_to_review
leadership_stories_to_prepare
weak_areas_to_defend
questions_to_ask_interviewer
seven_day_prep_plan
confidence
```

---

### Allowed Tools

None by default.

---

### Constraints

* Must not invent experience for interview stories
* Must identify weak areas honestly
* Must provide practical preparation steps
* Must stay aligned to the resume and job description

---

### Observability Events

* `interview_coach.started`
* `interview_coach.completed`
* `interview_coach.failed`

---

## 12. Tailoring Agent

### Purpose

The Tailoring Agent suggests resume improvements that better align the resume with a selected job.

It may improve:

* wording
* emphasis
* ordering
* clarity
* alignment to job terminology

It must not invent facts.

---

### Pattern

Controlled generation.

Evidence-bound output.

---

### Inputs

* original resume text or sections
* resume profile
* selected job description
* final resume review
* career advice
* tailoring constraints

---

### Outputs

Structured `TailoredResumeDraft`:

```text
headline_suggestions          (ADR-056 addendum #2 — the positioning tagline below the candidate's name)
summary_suggestions
experience_bullet_suggestions
skills_section_suggestions
overall_tailoring_notes       (ADR-056 addendum — strategy summary; 3-5 sentences <=120 words; positioning thesis + concrete moves)
fidelity_risk_summary
```

Each tailored bullet must include:

```text
original_text
suggested_text                (empty for claim_type="remove" or "gap")
supporting_evidence
claim_type                    Literal["reword", "emphasize", "gap", "remove"]   (ADR-056 added "remove")
fidelity_risk
section_label                 (ADR-056 — e.g. "headline", "summary", "experience:Acme:Staff Engineer", "skills")
impact_rationale              (ADR-056 addendum — one sentence <=25 words; references a concrete JD signal)
unsupported_claims
```

Page-budget contract (ADR-056): per-bullet `suggested_text` word count must
fall in `ceil(0.85 * original_words) .. floor(1.05 * original_words)` for
summary / experience / skills sections, relaxing to "match within +/- 3
words" for headlines.

---

### Allowed Tools

None by default.

Report generation is handled by a service, not the Tailoring Agent.

---

### Constraints

* No invented metrics
* No invented technologies
* No inflated job titles
* No fabricated leadership scope
* No new certifications
* No unsupported domain claims
* Missing experience must be labeled as a gap, not rewritten as if present
* Page count must not grow — `suggested_text` word count stays within the
  per-section length band (ADR-056)
* Net-new bullets are not allowed; `claim_type="remove"` is the only way to
  free space; `claim_type="gap"` is the only way to surface missing experience
* Every suggestion must declare a valid `section_label` and a non-generic
  `impact_rationale` that references the JD (ADR-056 addenda)

---

### Observability Events

* `tailoring_agent.started`
* `tailoring_agent.completed`
* `tailoring_agent.failed`

---

### Trigger Surfaces

Two paths invoke the Tailoring Agent. Both run the same agent with the same prompt and the same `TailoredResumeDraft` schema. Both are followed by a Fidelity Reviewer call without exception.

| Path | When it runs | How it is triggered | Approval mechanism |
|------|--------------|---------------------|--------------------|
| In-graph node | During a workflow run, after `interview_prep` (or `career_advice` skip) | `state["user_requested_tailoring"] = True` set before run start | LangGraph `interrupt()` at `await_tailoring_approval` (ADR-011) |
| Out-of-graph router | Post-workflow, per job, on demand | `POST /workflows/{wf}/jobs/{job}/tailor` (ADR-055) | `POST /tailorings/{id}/decision` writes `decision` column |

The out-of-graph path reads `resume_profile` and the job from the LangGraph checkpoint, and reads per-job `final_review` and `career_advice` from the relational repos. It exists because tailoring intent is fundamentally per-job, post-hoc, and repeatable — properties that don't fit the single-shot graph lifecycle.

---

## 13. Fidelity Reviewer Agent

### Purpose

The Fidelity Reviewer validates tailored resume output before it is accepted or presented as final.

It checks whether tailoring suggestions remain faithful to the source resume/profile.

---

### Pattern

Guardrail / validation pattern.

---

### Inputs

* original resume/profile
* tailored resume draft
* selected job description
* tailoring constraints
* ethics guardrails

---

### Outputs

Structured `FidelityReview`:

```text
overall_fidelity_status
unsupported_claims
fabricated_metrics
inflated_scope_flags
unsupported_technology_flags
unsupported_certification_flags
required_removals
required_revisions
approval_recommendation
confidence
```

---

### Allowed Tools

None.

---

### Constraints

* Must flag unsupported claims
* Must reject fabricated or inflated content
* Must prefer conservative wording
* Must require user approval before final export
* Must enforce the per-bullet length band and reject bullets that overflow
  (>1.05x original) OR collapse (<0.85x original); headline budget relaxes
  to "match within +/- 3 words" (ADR-056)
* Must validate `section_label` against the candidate's actual resume
  sections; missing or mismatched labels land in `required_revisions`
* Must reject `claim_type="remove"` with non-empty `suggested_text`
* Must reject generic `impact_rationale` (phrases like "stronger phrasing",
  "better impact" with no JD reference) and missing rationale
* Must reject strategy-summary opening with hedging or generic praise on
  any non-trivial draft (ADR-056 addendum #2)

---

### Observability Events

* `fidelity_reviewer.started`
* `fidelity_reviewer.completed`
* `fidelity_reviewer.failed`
* `fidelity_reviewer.unsupported_claim_detected`

---

### Trigger Surfaces

The Fidelity Reviewer always runs after the Tailoring Agent — there is no path that bypasses it. It runs both inside the workflow graph (paired with the in-graph tailoring node) and inside the on-demand tailoring router (ADR-055), with the same prompt and the same `FidelityReview` output schema in both cases. The output is persisted alongside the draft in `tailored_resumes.fidelity_review_json`.

The Fidelity Reviewer is also reused — unchanged — for the Resume Clinic (see
§13.1 below). The clinic runner packs clinic `RewriteSuggestion`s into a
`TailoredResumeDraft`-shaped envelope so the prompt's evidence-binding and
fabrication checks operate on the same inputs they expect for tailoring.

---

## 13.1 Resume Reviewer Agent (ADR-066)

### Purpose

Produces all three outputs of the standalone Resume Clinic in a single call:

1. A role-agnostic quality scorecard (always).
2. A target-role/track alignment read (when a target is given; otherwise null).
3. An evidence-bound overhaul = reorganization plan + per-bullet rewrites.

The clinic is **job-agnostic by construction** — it runs on the resume alone,
no JD, no scoring. This is the second product surface (ADR-066 motivation:
the funnel is senior-tuned and gates resume-facing help behind a scored
job; the clinic gives that help to anyone with a resume, regardless of
career stage).

### Pattern

Single structured-output agent. No reflection loop, no tool calls. The runner
chains it with the Fidelity Reviewer (which runs unchanged on the
agent-authored rewrites; evidence-binding holds with or without a job).

### Inputs

- `resume_profile` (parsed; cached in the second system block by PromptLoader).
- `target_role: str | None`, `target_track: "ic" | "architect" | "management" | None`.
- `seniority_aware: bool` — when true, calibrate findings/fixes/rewrites to
  the candidate's career stage as inferred from the resume.
- `role_data: dict | None` — optional grounding (occupation taxonomy)
  produced by the pluggable `RoleDataProvider`. v1 always None
  (`NullRoleDataProvider`). When non-null, the prompt treats it as ground
  truth for the alignment axis.

raw_text is NEVER in the reviewer context — same prompt rule as every other
resume-facing agent. raw_text goes to the Fidelity Reviewer only.

### Outputs

`ResumeClinicReview` (Pydantic, `app/schemas/resume_clinic.py`):

- `quality: ResumeQuality` — one rating per dimension (Literal enum, schema-
  enforced): `structure_ordering | impact_quantification | clarity |
  ats_formatting | consistency | length_fit | seniority_framing`, each rated
  `strong | adequate | needs_work` with `findings[]` and `fixes[]`, plus
  `overall_summary`.
- `alignment: Alignment | None` — `fit_summary`, `missing_skills[]`,
  `missing_keywords[]`, `suggested_certifications[]`, `suggested_projects[]`,
  `emphasize[]`, `confidence` (low / medium / high). Null when no target.
- `reorganization: Reorganization` — `section_order[]` plus `moves[]`
  (`action: move | cut | promote`, `subject`, `rationale`).
- `rewrites: list[RewriteSuggestion]` — same shape as tailoring's
  `TailoredBullet` minus the JD-relative fields: `section_label`,
  `original_text`, `suggested_text`, `claim_type` (one of `restate | reorder |
  quantify | reframe`), `supporting_evidence` (required, min_length=1).

### Constraints

- Never fabricate experience, metrics, scopes, technologies, dates, or
  certifications. Missing experience is labelled as a gap in
  `alignment.missing_skills` / `missing_keywords`, never rewritten as if
  present.
- All claim types and ratings are Literal enums; a drifted model emitting a
  free-text value fails Pydantic validation (catches the schema-passes /
  meaning-shifts failure shape ADR-058's pin invariant was shipped to
  expose).

### Observability Events

Same as every BaseAgent — `agent_started` / `agent_completed` / `agent_failed`,
plus one `llm_calls` row per call. The clinic runner correlates these to a
lightweight `workflow_runs` row (`workflow_type="resume_clinic"`,
`user_id=profile`) so the per-profile Cost Dashboard sees clinic spend.

---

## 14. Status / application tracking (intentionally absent)

There is **no status-manager component and no application-tracking feature**, by
design. An early `status_manager` service was removed (dead-code audit); the
decision points it would have recorded (Apply / Save / "marked applied") are
deliberately out of scope so the career decision stays human-owned (the
"No application tracking" rule in `CLAUDE.md`).

What the system does record instead is workflow *run* lifecycle and per-artifact
*decisions*, both deterministic and auditable:

* run status transitions on `workflow_runs` (incl. `cancelling`/`cancelled`,
  ADR-083) written only by the orchestrator / run wrappers;
* human decisions on tailorings and clinic reviews (`approve`/`revise`/`reject`/
  `edit`) persisted by the out-of-graph endpoints and mirrored to the
  `human_decisions` audit table (ADR-074).

No agent ever writes status. Agents return structured output; the orchestrator and
the REST endpoints own all state transitions.

---

## 15. Memory Agent / Memory Service

> **Designed, NOT wired into the runtime.** The `memory_items` table,
> `MemoryRepository`, and per-user scoping (ADR-062) exist, but no agent or
> workflow node reads or writes memory today, and there is no `MemoryService` /
> `app/memory/`. This section is the design contract for when memory is wired,
> not a description of current behavior (see `CLAUDE.md` and
> `state_and_memory_model.md`).

Memory is future-facing but should be modeled early.

The memory component should store structured learning across runs.

Examples:

```text
preferred roles
rejected job patterns
successful resume signals
preferred industries
companies to avoid
interview feedback
```

Memory should not be vague conversation history.

### Rules

* Memory must be structured
* Memory must be retrieved selectively
* Memory must not override current workflow state
* Memory must not be sent to every agent by default

---

## 16. Agent Input / Output Contract Standard

Every agent must define:

```text
Input schema
Output schema
Prompt file
Allowed tools
Version
Failure behavior
Observability events
Security constraints
```

No agent should be added without this information.

---

## 17. Agent Prompt Structure

Every agent prompt should follow this structure:

```text
Shared ethics guardrails
Agent role
Task objective
Input context
Output schema
Constraints
Failure/uncertainty behavior
```

Example:

```text
{{ethics_guardrails}}

# Role
You are the Resume Critic Agent.

# Task
Evaluate the resume against the selected job.

# Constraints
Do not fabricate experience.
Separate resume gaps from career gaps.

# Output
Return ResumeReview JSON.
```

---

## 18. Agent Observability Requirements

Every agent execution must log:

```text
workflow_id
agent_name
event_type
input_summary
output_summary
duration_ms
status
error_message
prompt_version
model_provider
model_name
```

If an agent participates in a loop, each round must be logged separately.

---

## 19. Agent Security Requirements

Agents must not:

* access the database directly
* access the filesystem directly
* call arbitrary URLs
* receive secrets
* follow instructions inside job descriptions
* store raw hidden reasoning
* fabricate outputs

Agents must:

* use approved tools only
* validate outputs
* respect PII minimization
* follow ethics guardrails

---

## 20. Per-Agent Provider and Model Assignment

Per ADR-053 (which supersedes ADR-051's static assignment), each agent is
mapped to a `(provider, model)` pair via a `ModelRegistry` that is built once
at backend startup from the merged effective config.

* Defaults match the ADR-051 tiering (Haiku for high-volume / validation,
  Sonnet for generative / advisory).
* Users may override any agent's `provider` and `model` via the Settings UI.
  Overrides are stored in `user_config` under the dotted keys
  `agents.{agent_name}.provider` and `agents.{agent_name}.model`.
* Only models present in the `ModelRegistry`'s known set are accepted. The
  registry is the single source of truth for what providers and models the
  system supports; adding a new model requires a code change.
* Switching an agent's model requires saving via Settings and **restarting
  the backend**. In-flight workflows continue with the assignment they
  started under.

This is the layer that lets the system route around per-provider rate limits
and tune cost per agent. The cost rollup in the run report and Workflow Detail
shows actual `(provider, model, calls, tokens, cost)` per agent so the user
can rebalance the next run with real data.

---

## 21. Final Agent Model Principle

The agent model should remain disciplined:

> Use agents where reasoning is valuable. Use services where execution must be reliable. Use the orchestrator to control the system.
