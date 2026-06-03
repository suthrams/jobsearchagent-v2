# ADR-077: Attribute Failed LLM-Call Spend + Cost-Logging Completeness Invariant

## Status

Accepted (2026-06-03). **Implemented** — a billed-but-unparseable LLM response
(schema repair exhausted) now writes an `llm_calls` row instead of being lost;
schema-repair token spend is accumulated across the two billed attempts; and a
forcing-function test guards that the agent failure path logs the cost. Extends
ADR-027 (cost/token tracking) and the thread-local-race fix that ADR-074 Gap 4
started. No schema change.

## Context

`llm_calls` is the truth source for cost: `cost_breakdown`, the Cost Dashboard,
`system_health.run_metrics_rollup`, and per-profile spend all derive from it
(ADR-074). The wiring has a residual hole on the **failure path**, found while
auditing our own observability for Article 11.

`BaseAgent._run` logs `log_llm_call` on the **success path only** (inside the
`try`, after `complete_with_usage` returns). Any exception jumps to the `except`,
which calls `log_agent_failed` (duration + error string, **no tokens**). So a call
that raises writes **zero** `llm_calls` rows.

That matters because of how `ClaudeProvider.complete()` fails. The flow is
`_invoke_with_retry` (transient retries) -> optional `_attempt_schema_repair`
(once) -> `_log_call` -> `_extract_dict`. `_extract_dict` raises
`LLMProviderError` when `parsing_error` is still set after repair. But the raw
`AIMessage` carries `usage_metadata` **even on parse failures** (the provider's own
comment at `claude_provider.py:171`), and `_log_call` runs at line 147 *before*
`_extract_dict` raises at line 148. So when schema repair is exhausted, **the
response was billed** (the model produced output, it just did not parse) yet the
spend never reaches `llm_calls`. Every downstream cost view undercounts it.

Two related defects in the same path:

1. **Lost failed-call spend (primary).** Billed-but-unparseable completions write
   no cost row. Scope is precise: a *transient* failure (rate-limit / connection /
   500 exhausted in `_invoke_with_retry`) is generally not billed, so logging
   nothing there is correct; the undercount is specifically the
   **schema-repair-exhausted** case.
2. **Repair-attempt undercount.** When a repair fires, `_extract_usage` reads only
   the final (repair) `AIMessage`. The first, parse-failed attempt was also billed,
   and its tokens are dropped — an undercount even when the repair *succeeds*.

And the deeper, systemic gap:

3. **No completeness invariant.** "Every billed call writes `llm_calls`" is
   enforced by convention across three independent copies of the pattern
   (`BaseAgent._run`, the resume-parser fn in `claude_provider.py`,
   `CustomUrlScraper`). Nothing fails the build if a call site (or a refactor of
   these) drops the cost row. The original cost-undercount bug shipped for exactly
   this reason; this is the lesson in `feedback_test_invariants_for_critical_concerns`.

This is the same correctness class as the thread-local race the observability story
narrates: cost attribution that looks complete but silently undercounts. The
happy path was instrumented thoroughly; the failure path, where cost hides, was
left under-instrumented.

## Decision

**Make billed spend attributable even when the call fails, and guard the
completeness with a forcing-function test.**

1. **Carry usage on the exception.** `LLMProviderError` gains an optional
   `usage: LLMUsage | None` attribute (default `None`). `ClaudeProvider.complete()`
   attaches the extracted usage when it raises the schema-repair `LLMProviderError`
   — the response was billed, so the tokens are known. Transient failures attach
   nothing (no billed usage to attribute), so they correctly log no cost row.

2. **Log the failed-call cost.** `BaseAgent._run`'s `except` path reads
   `getattr(exc, "usage", None)`; if it carries non-zero tokens, it writes one
   `log_llm_call` row for the failed call before re-raising. The matching
   `agent_events` row (`status="failed"`, already written) carries the failure
   signal, so no `llm_calls` schema change is needed: cost lives in `llm_calls`,
   the failure flag lives in `agent_events`, correlated by `workflow_run_id` +
   `agent_name`. A failed-but-billed call therefore now counts as real spend in
   every rollup (more accurate, not less).

3. **Accumulate repair spend.** `complete()` captures the first (parse-failed)
   attempt's usage before repair and sums it into the logged total, so a repaired
   call bills for both attempts.

4. **Completeness forcing function.** `tests/v2/test_cost_logging_completeness.py`:
   a behavioral test that a provider raising `LLMProviderError` with `.usage`
   attached causes `BaseAgent._run` to log exactly one `llm_calls` row (and still
   re-raise), plus a source-scan asserting the failure-path `log_llm_call` is
   present and that the known LLM-call sites each log cost. Mirrors
   `test_security_events.py` / `test_step_executions.py`.

### Why no `llm_calls` schema change

Adding a `status`/`failed` column would touch the table, the repository, every
rollup query, and the read services. The failure signal already exists in
`agent_events`; a failed-but-billed call is, for cost purposes, just spend. Keeping
`llm_calls` as the pure spend ledger and `agent_events` as the lifecycle log
preserves the minimal-surface design (the same reasoning ADR-076 used to reuse
`security_events`).

## Consequences

- **Positive.** Schema-repair-exhausted spend is attributed instead of vanishing;
  repaired calls bill for both attempts; the completeness invariant is now guarded,
  closing the seam that let the original undercount ship.
- **Semantic shift (documented).** `llm_calls` now includes billed-but-failed
  completions, so `COUNT(*)`-style call totals include them. This is intentional —
  they are real spend — and the failure is still distinguishable via `agent_events`.
- **Known residual (named, not hidden).** Only `ClaudeProvider` attaches usage on
  failure. `OpenAIProvider` failed-call spend stays unattributed until it does the
  same; the `BaseAgent` side is provider-agnostic and will pick it up for free once
  it does. Tracked as follow-up debt.
- **Cost.** One extra never-crash append on the rare failure path; negligible.

## Alternatives considered

- **Read provider thread-local usage on the except path.** Rejected: on a
  *transient* failure the thread-local holds the *previous* successful call's usage
  (stale), which would double-count. Attaching usage to the specific exception is
  unambiguous.
- **Add a `status` column to `llm_calls`.** Rejected: schema churn across the whole
  read stack for a signal `agent_events` already carries.
- **Acknowledge, do not fix.** Rejected: this is a cost-correctness defect in a
  system whose stated thesis is that cost attribution is load-bearing; leaving it
  would undercut the article's own argument.
