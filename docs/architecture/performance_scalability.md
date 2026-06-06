# Performance and Scalability Model – jobsearchagent-v2

---

## 1. Purpose

This document defines how **jobsearchagent-v2** manages:

* performance
* cost
* responsiveness
* scalability over time

The system uses:

* multiple workflows
* multiple agents
* LLM calls
* reflection loops
* ReAct-based research
* human-in-the-loop pauses

Without explicit controls, these can lead to:

* slow execution
* high cost
* poor user experience
* unstable behavior

---

## 2. Core Strategy

The system follows this principle:

```text
Score many jobs cheaply → Deeply analyze only selected jobs
```

This is the most important performance optimization.

---

## 3. Performance Goals

| Area                     | Goal                        |
| ------------------------ | --------------------------- |
| Job discovery            | < 5–10 seconds              |
| Scoring (batch)          | < 1–2 seconds per job       |
| Deep review (single job) | < 20–40 seconds             |
| Reflection loop          | bounded to small iterations |
| Report generation        | < 5 seconds                 |
| UI responsiveness        | near real-time updates      |

---

## 4. Execution Model

### Initial Model

```text
Sequential execution
```

* simpler to implement
* easier to debug
* sufficient for the SQLite system under sequential use (incl. multiple profiles, ADR-062)

---

### Future Model

```text
Parallel execution
```

* parallel scoring
* parallel research
* parallel deep reviews (multi-job)

The architecture is designed to support this without redesign.

---

## 5. Workflow Optimization

### 5.1 Job Discovery

Optimizations:

* limit number of jobs fetched
* stop when enough relevant jobs found
* optional posting-age cap drops stale postings at discovery, before any scoring spend (`search.max_posting_age_days`, ADR-080)
* cache results per query (future)

---

### 5.2 Scoring Workflow

Optimizations:

* optional relevance pre-filter: one cheap batched LLM call (Haiku) drops clear seniority/relevance mismatches BEFORE scoring, so the per-job research+scoring spend is never paid on the noise (`search.relevance_filter`, ADR-079) — net cost-negative on a noisy profile
* batch processing
* no ReAct
* no reflection
* minimal prompt size
* reuse normalized job structure

---

### 5.3 Deep Review Workflow

Optimizations:

* only run on selected jobs
* reuse research context across steps
* avoid redundant LLM calls
* pass minimal context to each agent

---

## 6. Bounded Execution Controls

All workflows must enforce limits.

### Limits

```text
MAX_JOBS_PER_RUN = 10   # default scored cap; per-run override up to MAX_SCORED_CEILING=25 (ADR-061)
MAX_DISCOVERED_JOBS = 50 # wide-net cap (manual selection / relevance filter); search.max_discovered (ADR-060/061)
MAX_SELECTED_JOBS = 3    # qualifying jobs that reach in-graph deep review (cost cut from 10)

MAX_RESEARCH_STEPS = 2
MAX_REVIEW_ROUNDS = 2    # cost cut from 3

MAX_LLM_CALLS_PER_JOB = 10
MAX_LLM_CALLS_PER_RUN = 200  # global per-run budget backstop

MAX_COST_PER_RUN = configurable
```

---

### Why It Matters

Prevents:

* runaway loops
* excessive cost
* long execution times
* unpredictable behavior

---

## 7. LLM Optimization

### 7.1 Token Control

Reduce token usage by:

* sending structured inputs instead of raw text
* avoiding unnecessary context
* truncating long job descriptions
* summarizing research output

---

### 7.2 Prompt Size

Rules:

* include only relevant fields
* avoid sending full workflow state
* avoid sending raw logs
* avoid sending memory unless needed

---

### 7.3 Model Selection (Future)

Different tasks may use different models:

| Task      | Model Type            |
| --------- | --------------------- |
| Scoring   | fast/cheap            |
| Research  | reasoning-capable     |
| Critique  | high-quality          |
| Tailoring | controlled generation |

---

## 8. Reflection Loop Optimization

The reflection loop must be strictly bounded.

### Rules

```text
MAX_REVIEW_ROUNDS = 2
```

### Stop Conditions

* audit_score ≥ threshold
* no meaningful improvement
* diminishing returns
* max rounds reached

---

### Optimization Strategy

* reuse prior feedback
* avoid full re-analysis each round
* track improvement deltas

---

## 9. ReAct Optimization

ReAct is used only in the Research Agent.

### Rules

```text
MAX_RESEARCH_STEPS = 2
```

### Optimization Strategy

* summarize observations
* stop early if sufficient context
* avoid unnecessary tool calls

---

## 10. Data Access Optimization

### SQLite Considerations

SQLite is sufficient for:

* sequential workloads (a single run at a time, across one or more profiles — ADR-062)
* local execution
* moderate data volumes

---

### Optimization Strategies

* index frequently queried fields
* avoid large JSON scans
* fetch only required columns
* keep state_json reasonably sized

---

## 11. Caching Strategy (Future)

### Candidates for caching

* job discovery results
* normalized job structures
* research context per job
* scoring outputs

---

### Cache Key Examples

```text
job_search:{criteria_hash}
research:{job_id}
score:{resume_id}:{job_id}
```

---

## 12. Memory and Context Optimization

Memory should be:

* selectively retrieved
* summarized
* limited in size

Do not:

* inject full memory set into prompts
* use memory as a substitute for state

---

## 13. Observability for Performance

Track performance metrics:

### Per Workflow

```text
total_duration_ms
total_llm_calls
total_tokens_input
total_tokens_output
total_cost
```

### Per Agent

```text
duration_ms
tokens_used
success/failure
```

---

### Why It Matters

Enables:

* bottleneck detection
* cost tracking
* optimization decisions

---

## 14. UI Responsiveness

The UI should:

* show progress per step
* display intermediate results
* indicate waiting states (HITL)
* avoid blocking interactions

---

### Example Timeline

```text
Fetching jobs...
Scoring jobs...
Waiting for selection...
Running deep review...
Generating report...
```

---

## 15. Scalability Strategy

### Current State

```text
SQLite-based system; multiple profiles under SEQUENTIAL use (ADR-062)
```

The system serves multiple profiles (one resume / config / memory / history per
profile), but use is sequential: one person's search runs at a time and you
switch profiles between runs. This lets the global singletons (compiled graph,
WorkflowDependencies, agent registry) stay as-is and be rebuilt on profile switch
/ run kickoff rather than partitioned per user. Concurrent multi-user execution
is still future work.

---

### Future Scaling Path

```text
SQLite → Postgres
Local execution → API-based backend
Sequential → parallel execution
Sequential multi-profile (ADR-062) → concurrent multi-user (auth + per-user runtime)
```

---

### Design Requirement

The current architecture must not prevent:

* parallelization
* distributed execution
* database migration

---

## 16. Cost Control

LLM usage is the primary cost driver.

### Controls

```text
limit number of jobs scored
limit number of deep reviews
limit reflection loops
limit ReAct steps
limit tokens per prompt
limit total LLM calls
```

---

### Example

```text
10 jobs scored cheaply
2 jobs deeply analyzed
```

---

## 17. Failure Handling and Performance

Failures should not cascade.

### Strategy

```text
Retry once → fallback → continue or fail gracefully
```

---

### Examples

| Failure                | Action                   |
| ---------------------- | ------------------------ |
| scraper fails          | fallback to manual input |
| LLM fails              | retry once               |
| schema fails           | reject output            |
| reflection loop stalls | stop loop                |

---

## 18. Anti-Patterns to Avoid

Avoid:

* unbounded loops
* sending entire state to LLM
* repeated identical LLM calls
* excessive logging of large payloads
* blocking UI while waiting for long operations
* deep review on all jobs
* uncontrolled memory injection

---

## 19. Testing Performance

Tests should validate:

* execution within bounds
* LLM call limits respected
* reflection loop stops correctly
* research steps limited
* cost estimation accurate

---

## 20. Final Principle

Performance and scalability are not achieved through infrastructure alone.

They are achieved through:

```text
bounded execution
controlled reasoning
selective depth
efficient data flow
```

The system remains efficient because it chooses:

```text
where to think deeply
and where not to
```
