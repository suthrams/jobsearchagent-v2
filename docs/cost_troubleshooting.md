# Cost Troubleshooting Guide

A practical step-by-step for diagnosing and reducing API spend in
jobsearchagent-v2. Cost is a primary architectural concern for this
system — it directly determines how many career-research sessions a
budget supports.

This guide assumes you have:
- The Streamlit UI running (`streamlit run app/ui/streamlit_app.py`)
- The FastAPI backend running (`uvicorn app.api.main:app --reload`)
- Access to `data/v2.db` via `sqlite3` (or any SQLite client)
- Access to your provider's billing console
  (console.anthropic.com → Usage & Billing for Claude;
  platform.openai.com → Usage for OpenAI)

> **Background.** The system uses Claude (and optionally OpenAI) per
> agent. Per-agent model assignment lives in
> `app/providers/model_registry.py::DEFAULT_AGENT_ASSIGNMENT` and can be
> overridden in **Settings → Agent Models** in the UI. Cost is recorded
> per LLM call in `llm_calls` and rolled up per run in `run_metrics`
> after a workflow completes. See ADR-053 (per-agent provider/model
> selection) and `docs/architecture/data_model.md` for schema details.

---

## Quick reference

| Question | Answer location |
|---|---|
| What did this run cost? | Workflow Detail → Diagnostics → Cost Breakdown |
| Which agent dominated cost? | Per-run cost query (Step 3 below) |
| Is the local total accurate? | Reconciliation query (Step 4 below) |
| Can I cut cost without losing quality? | Lever decision matrix (Step 6 below) |
| Did my last cut work? | Compare run-over-run query (Step 8 below) |

---

## Step 1 — Sanity-check that observability is recording calls

Before troubleshooting cost, verify the audit trail is being written.
A previous bug (fixed; see CHANGELOG 2026-05-05 "Observability gap")
left `llm_calls` empty in production for weeks despite real spend.

```sql
-- Are LLM calls being recorded at all?
SELECT COUNT(*) AS total_llm_calls,
       COUNT(DISTINCT workflow_run_id) AS distinct_runs
FROM llm_calls;
```

**Expected:** non-zero `total_llm_calls` for every run that completed
after the observability fix shipped. **If zero on recent runs:** your
backend may be running an older code version, or `BaseAgent._run` was
modified again. Restart `uvicorn` from the current code, then check
`tests/v2/test_cost_invariants.py` is passing — those tests exist
specifically to catch this regression.

---

## Step 2 — Find the run you want to investigate

Open **Workflow History** in the Streamlit UI. The table shows status,
stage, cost, and ID for every run. Click the row that's interesting,
then **Open detail →**.

For a SQL list:

```sql
SELECT id,
       status,
       current_step,
       started_at,
       completed_at,
       json_extract(state_json, '$.run_metrics.estimated_cost_usd') AS state_cost,
       error_message
FROM workflow_runs
ORDER BY started_at DESC
LIMIT 20;
```

Note: `state_cost` is the in-memory aggregator. The truth source is
`llm_calls` (Step 3).

---

## Step 3 — Per-agent cost breakdown for one run

The most useful query for cost diagnosis. Identifies the agent + model
combination that dominated spend.

```sql
SELECT agent_name,
       provider,
       model,
       COUNT(*)                              AS calls,
       SUM(tokens_input)                     AS tokens_in,
       SUM(tokens_output)                    AS tokens_out,
       ROUND(SUM(estimated_cost), 4)         AS cost_usd,
       ROUND(AVG(latency_ms))                AS avg_latency_ms,
       ROUND(MAX(latency_ms))                AS max_latency_ms
FROM llm_calls
WHERE workflow_run_id = '<paste-workflow-id-here>'
GROUP BY agent_name, provider, model
ORDER BY cost_usd DESC;
```

In the UI, the same data is at **Workflow Detail → Diagnostics → Cost
Breakdown**.

**What to look for:**
- One agent at >60% of run cost is the obvious lever.
- A `claude-sonnet-4-6` row that's also the highest call count: prime
  candidate to move to Haiku.
- Unexpectedly high `avg_latency_ms` on an agent suggests retries or
  prompt-cache misses — see Step 5.

---

## Step 4 — Reconcile against the provider billing console

The local cost estimate uses fixed per-million-token rates from
`app/providers/claude_provider.py::_PRICING` and
`app/providers/openai_provider.py::_PRICING`. The provider's billing
console is the truth source. They will not match exactly:

| Source | Counts |
|---|---|
| `llm_calls.estimated_cost` (sum) | Successful calls only, at our local rate table |
| Anthropic Usage page | All calls including retried-and-failed, at the rate Anthropic actually billed |
| Cached input tokens | Anthropic console reports these separately; our `tokens_input` column lumps them together |

```sql
-- Local total for a date range
SELECT DATE(created_at)               AS day,
       COUNT(*)                       AS calls,
       SUM(tokens_input)              AS tokens_in,
       SUM(tokens_output)             AS tokens_out,
       ROUND(SUM(estimated_cost), 2)  AS local_cost_usd
FROM llm_calls
WHERE created_at >= '2026-05-01'
GROUP BY DATE(created_at)
ORDER BY day DESC;
```

Compare day-by-day to the Anthropic Usage page. **A gap of 10-20% is
normal** (cache pricing differences, our rate table going stale,
retries). **A gap of 2x or more means something is wrong** — open an
issue and look at:

- Schema-repair retries on the failing agent: each schema-repair attempt
  is billed but our aggregator only counts the final successful response.
- A workflow stuck in `running` for hours with concurrent retries.
- The PRICING tables in the provider files being out of date relative to
  Anthropic's current published rates.

---

## Step 5 — Identify cost drivers across all runs

For weekly or monthly trend analysis:

```sql
-- Per-agent cost across the last 7 days
SELECT agent_name,
       model,
       COUNT(*)                              AS calls,
       SUM(tokens_input + tokens_output)     AS total_tokens,
       ROUND(SUM(estimated_cost), 2)         AS cost_usd
FROM llm_calls
WHERE created_at >= datetime('now', '-7 days')
GROUP BY agent_name, model
ORDER BY cost_usd DESC;
```

```sql
-- Per-run total cost across the last 7 days
SELECT workflow_run_id,
       MIN(created_at)                  AS started_at,
       COUNT(*)                         AS calls,
       ROUND(SUM(estimated_cost), 4)    AS cost_usd
FROM llm_calls
WHERE created_at >= datetime('now', '-7 days')
GROUP BY workflow_run_id
ORDER BY cost_usd DESC;
```

```sql
-- Top 5 most expensive single calls (latency-tail or retry-heavy)
SELECT workflow_run_id,
       agent_name,
       model,
       tokens_input,
       tokens_output,
       ROUND(estimated_cost, 4) AS cost_usd,
       latency_ms,
       created_at
FROM llm_calls
ORDER BY estimated_cost DESC
LIMIT 5;
```

---

## Step 6 — Pick the right lever

| Symptom | Lever | How to apply |
|---|---|---|
| One agent on Sonnet dominates cost | Move that agent to Haiku | Settings → Agent Models, pick `claude-haiku-4-5-20251001`, restart backend |
| Many low-quality jobs reach deep review | Raise `min_match_score` | Settings → Scoring, set 80 or 85, no restart needed |
| Deep review fans out to too many jobs | Lower `MAX_SELECTED_JOBS` | Edit `app/workflows/limits.py`, restart backend |
| Reflection loop runs 3 rounds rarely changing verdict | Lower `MAX_REVIEW_ROUNDS` to 2 | Edit `app/workflows/limits.py`, restart backend |
| Claude rate-limited; OpenAI key set | Move high-volume agents (`research_agent`, `scoring_agent`) to `gpt-4o-mini` | Settings → Agent Models, pick `gpt-4o-mini` for those agents, restart |
| Total spend ballooning across many runs | Cut tailoring iterations | Limit yourself to 1-2 drafts per job; each draft is ~$0.015-0.025 on Sonnet |
| Same job re-discovered and re-scored across runs | Exclude it (ADR-057) | Workflow Detail → Find & Score → row → 🚫 Exclude. URL-based dedup at next discovery prevents re-scoring. |

For the recommended baseline assignment per agent, with the reasoning
for each pick, see [`docs/model_recommendations.md`](model_recommendations.md).
That doc also covers the escalation order if budget pressure mounts and
the symptoms that signal an agent should be upgraded.

**Rate reference** (per 1M tokens, May 2026):

| Model | Input | Output | Where defined |
|---|---|---|---|
| `claude-haiku-4-5-20251001` | $0.25 | $1.25 | `app/providers/claude_provider.py:_PRICING` |
| `claude-sonnet-4-6` | $3.00 | $15.00 | same |
| `claude-opus-4-7` | $15.00 | $75.00 | same |
| `gpt-4o-mini` | $0.15 | $0.60 | `app/providers/openai_provider.py:_PRICING` |
| `gpt-4o` | $2.50 | $10.00 | same |
| `o1` | $15.00 | $60.00 | same |

Sonnet → Haiku is **12x cheaper**. Sonnet → gpt-4o-mini is **20-25x
cheaper**.

---

## Step 7 — Pre-flight a planned change

Before applying a lever, estimate the impact. Use the per-agent breakdown
from Step 3 and the rate table from Step 6.

Example: if `resume_critic` cost $0.40 in your last run on Sonnet,
swapping it to Haiku divides that by 12 → ~$0.033. Do this for each
lever you're considering before making the change.

If you have multiple recent runs, get an average cost per lever target:

```sql
SELECT agent_name,
       COUNT(DISTINCT workflow_run_id)   AS runs,
       ROUND(AVG(estimated_cost), 4)     AS avg_cost_per_call,
       SUM(estimated_cost) /
         COUNT(DISTINCT workflow_run_id) AS avg_cost_per_run
FROM llm_calls
WHERE created_at >= datetime('now', '-30 days')
GROUP BY agent_name
ORDER BY avg_cost_per_run DESC;
```

---

## Step 8 — Verify the change worked

After applying a lever and restarting the backend, run a **clean test
workflow** with the same search criteria as a previous expensive run.
Then compare:

```sql
SELECT workflow_run_id,
       MIN(created_at)               AS started_at,
       SUM(estimated_cost)           AS cost_usd,
       SUM(tokens_input)             AS tokens_in,
       SUM(tokens_output)            AS tokens_out
FROM llm_calls
WHERE workflow_run_id IN (
    '<old-expensive-run-id>',
    '<new-test-run-id>'
)
GROUP BY workflow_run_id
ORDER BY started_at DESC;
```

If the new run is meaningfully cheaper at acceptable quality, keep the
change. If quality dropped (e.g. the resume critic now misses obvious
gaps), the answer might be one of:

- Keep the cheap default but allow per-run overrides for high-stakes jobs.
- Move only some agents to the cheaper model (e.g. critic to Haiku, but
  keep advisor on Sonnet).
- Tighten the prompt so the cheaper model performs better.

---

## Step 9 — Set up a budget alert

Anthropic and OpenAI both support spending alerts. Configure them:

- **Anthropic**: console.anthropic.com → Settings → Plans & Billing →
  Spend alerts. Set a daily and monthly cap.
- **OpenAI**: platform.openai.com → Settings → Billing → Usage limits.
  Same idea.

These trigger before you hit a hard balance ceiling. The credit-balance
error this system surfaced (ADR pending — see CHANGELOG 2026-05-05) was
non-retryable and crashed the workflow mid-run.

---

## Step 10 — Capture findings as memory

If a particular configuration wins consistently, save it. The system has
a `memory_items` table for cross-run learning. Manually capture:

```sql
-- Example: record a winning per-agent assignment
INSERT INTO memory_items (id, memory_type, memory_key, memory_value_json,
                          confidence, source_workflow_run_id,
                          created_at, updated_at)
VALUES (
    lower(hex(randomblob(16))),
    'cost_optimization',
    'agent_assignment_2026_05',
    '{"resume_critic": "haiku", "career_advisor": "sonnet",
      "interview_coach": "haiku-experimental", "tradeoff_notes": "..."}',
    80,
    '<workflow-id-that-validated-this>',
    datetime('now'),
    datetime('now')
);
```

This is currently a manual operation. A future iteration can wire memory
service surfaces into the Settings UI.

---

## When the numbers don't add up

If `llm_calls.SUM(estimated_cost)` is materially lower than the
provider's billing console (more than ~20% gap), do not just trust the
local number. Common causes, in order of likelihood:

1. **Schema-repair retries** — the provider was billed for the failed
   first attempt + the repair attempt; we only count the successful
   response. Look for `error_message` patterns in `workflow_runs` that
   mention "validation errors" or "schema repair".
2. **Concurrent retries from a stuck run** — a workflow in `running`
   status that hit a transient error may have retried in the background
   beyond what the audit trail shows. Find with: `SELECT id, status,
   started_at FROM workflow_runs WHERE status = 'running'`.
3. **Stale rate table** — `_PRICING` in the provider files may have
   drifted from Anthropic's current published rates. Compare to the
   provider's pricing page and update if needed.
4. **Different DB** — if you've been developing across machines or
   wiped `data/v2.db`, prior spend won't be in the local audit trail.
   The provider's view is cumulative; ours is per-database-instance.

---

## Reference: invariants this guide depends on

The cost-troubleshooting workflow assumes these invariants hold. They're
encoded as tests in `tests/v2/test_cost_invariants.py` and run on every
test invocation. **If any of these fail, this guide is not safe to use
until the underlying observability is fixed.**

| Invariant | Test |
|---|---|
| Every successful agent run writes one `llm_calls` row | `test_invariant_every_agent_run_writes_one_llm_call_row` |
| N runs produce N rows (no aggregation in BaseAgent) | `test_invariant_n_agent_runs_produce_n_llm_call_rows` |
| `llm_calls` captures provider + model + cost | `test_invariant_llm_calls_captures_provider_model_and_cost` |
| Every workflow gets a `run_metrics` row at register | `test_invariant_register_run_creates_run_metrics_row` |
| `run_metrics` is finalized from `llm_calls` (truth source) | `test_invariant_generate_report_finalizes_run_metrics_from_llm_calls` |
| `agent_events` completion count = `llm_calls` count | `test_invariant_agent_events_completed_equals_llm_calls_count` |
| Failed agent runs leave a failed event but no `llm_calls` row | `test_invariant_failed_runs_do_not_write_llm_call_row` |

---

## Why this guide exists

A previous bug left `llm_calls` and `run_metrics` empty in production
for weeks. Per-module unit tests passed; nothing tested the system-level
promise that "every billed call is auditable." When the user hit the
Anthropic credit ceiling, there was no way to attribute the spend.

The fix: invariant tests above + this guide. Cost is a primary
operational concern for this system; it deserves a guide and
non-negotiable tests, not just per-module coverage.

See CHANGELOG 2026-05-05 for the full diagnosis and fix narrative.
