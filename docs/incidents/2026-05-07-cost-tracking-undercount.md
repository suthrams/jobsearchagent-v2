# 2026-05-07 — Cost-tracking undercount (~3-4x)

**Severity:** High. Budget projections were wrong by a factor (not a percentage).
A run reported as `~$0.17` was actually billing `~$0.47`. Caught by the user
reconciling the Cost Dashboard against the Anthropic billing console.

**Fix commit:** [`6cb0048`](https://github.com/suthrams/jobsearchagent-v2/commit/6cb00483dfa1388d4de9fb91f7aad327567963b7)

---

## Symptom

The Cost Dashboard total reported by `compute_run_totals_from_llm_calls`
tracked consistently lower than what the Anthropic billing console showed
for the same time window. The gap was wide enough to invalidate the
documented `~$0.17/run` baseline and the `~110-130 runs / $25 budget`
projection in `docs/model_recommendations.md`.

---

## Root causes — four independent defects, all pushing in the same direction

### RC1 — Stale Haiku 4.5 pricing constant

**What:** `app/providers/claude_provider.py::_PRICING` had
`claude-haiku-4-5-20251001` at `$0.25 / $1.25` per million tokens. Actual
Anthropic public rate: `$1.00 / $5.00`.

**How it got there:** The constant was likely copied from Haiku 3 / 3.5
when the Haiku 4.5 model id was registered. No test asserted the constant
against an external source of truth, and the same wrong rate was duplicated
into `docs/model_recommendations.md`, which became the reference everyone
read.

**Magnitude:** ~4x undercount on every Haiku call. With 8 of 10 agents on
Haiku per `DEFAULT_AGENT_ASSIGNMENT`, this was the dominant factor.

### RC2 — Prompt-cache tokens never accounted for

**What:** `_extract_usage` only read `usage_metadata["input_tokens"]` and
`["output_tokens"]`. It ignored `input_token_details.cache_creation`
(billed at `1.25x` input rate) and `input_token_details.cache_read`
(billed at `0.10x` input rate).

**How it got there:** Prompt caching was added in `PromptLoader` (two
`cache_control: ephemeral` breakpoints) as a perf/cost optimization. The
cost-extraction path in the provider was never updated to read the cache
splits LangChain emits. Both changes were correct in isolation; nobody
owned the seam between them.

**Magnitude:** Variable but always non-zero. Every Claude call has cache
writes on the first call and cache reads on subsequent calls within the
5-min window. None of those tokens hit the cost rollup.

### RC3 — Resume parser bypassed the audit-write path

**What:** `make_resume_enhance_fn` called `provider.complete()` directly.
The cost was captured in the provider's thread-local `last_usage` but
never written to the `llm_calls` table.

**How it got there:** `BaseAgent._run()` is the standard path that writes
`llm_calls`. The resume parser predates that path (or was written to
avoid coupling to it) and was never migrated.
`compute_run_totals_from_llm_calls` is documented as "the truth source"
and reads only `llm_calls`. Anything not in that table is invisible to
the rollup, by design.

**Magnitude:** Episodic. Only fires on cache miss for a fresh resume
parse, but uses Sonnet, so a few cents per occurrence.

### RC4 — Custom URL extractor had the same bypass

**What:** `CustomUrlScraper._extract_via_llm` called `self._llm.complete()`
directly with the same shape as RC3.

**How it got there:** Same root pattern. Service code outside `app/agents/`
does not inherit `BaseAgent` and re-implemented the LLM call inline
without the audit hook.

**Magnitude:** Episodic per custom-URL fallback per run, Sonnet-priced.

---

## Why it was silent

1. **The truth source had no external reconciliation.**
   `compute_run_totals_from_llm_calls` was internally consistent — it
   summed what was in `llm_calls` correctly. There was no check comparing
   that sum to Anthropic's billing console. Both wrong rates and missing
   rows produced lower numbers, with no second opinion.
2. **Documentation reinforced the wrong rate.**
   `docs/model_recommendations.md` had the same `$0.25 / $1.25` Haiku row
   and a derived `$0.17/run` baseline. When users saw
   `Cost Dashboard ~ $0.17` and `docs say ~ $0.17`, the system looked
   correct.
3. **Tests asserted the wrong value as correct.**
   `test_estimate_cost_haiku` literally encoded
   `assert abs(cost - 1.50) < 0.01` with the comment
   `# 0.25 + 1.25 per million`. The test passing meant "the constants
   match the test", not "the constants match Anthropic". Textbook case of
   the rule in
   `feedback_test_invariants_for_critical_concerns.md` — module-mock unit
   tests do not catch a system-level invariant violation.
4. **Bypasses for non-BaseAgent LLM calls were silent.**
   No invariant test asserted "every LLM call writes one `llm_calls`
   row." The bypass agents were observable in `last_usage` but invisible
   to the rollup, and nothing flagged the divergence.
5. **Caching was added on the perf side without a corresponding cost-side
   update.** Two correct changes, one un-owned seam.

The dashboard was internally self-consistent (sum of `llm_calls` rows
equaled dashboard total), so the bug looked like rounding, not load-bearing
math being wrong. Tests, docs, dashboard, and constants all agreed with
each other. The only disagreement was with Anthropic's billing console — an
external system nobody had wired into the test loop.

---

## Fixes (commit [`6cb0048`](https://github.com/suthrams/jobsearchagent-v2/commit/6cb00483dfa1388d4de9fb91f7aad327567963b7))

| RC | Fix |
|---|---|
| RC1 | `_PRICING` corrected to `$1.00 / $5.00` for Haiku 4.5. `docs/model_recommendations.md` corrected. `test_estimate_cost_haiku` assertion updated to `$6.00` for 1M-in / 1M-out. |
| RC2 | `_extract_usage` returns 4-tuple including cache splits. Reads `usage_metadata.input_token_details` first; falls back to `response_metadata.usage.cache_*_input_tokens` for legacy payloads. New `_estimate_cost_with_cache` applies `1.25x` (writes) and `0.10x` (reads) multipliers. New tests cover both payload shapes and the cache math. |
| RC3 | `make_resume_enhance_fn` takes optional `observability`; closure accepts `workflow_id` and writes `log_llm_call` when both wired. `parse_pdf` / `parse_text` thread `workflow_id`. `load_resume` node passes it. `dependencies.py` wires `obs`. |
| RC4 | `CustomUrlScraper` takes keyword-only `observability` and `workflow_id`. New `_record_llm_call` after each LLM-fallback. Factory signature `(urls, workflow_id) -> scraper`. `discover_jobs` node passes `workflow_id`. |

---

## Validation

Live run on 2026-05-07:

| Source | Amount |
|---|---:|
| Anthropic console (delta of pre-paid balance) | `$0.47` |
| Cost Dashboard (`compute_run_totals_from_llm_calls`) | `$0.4588` |
| Gap | `~2.4%` undercount |

Within the 20% tolerance documented in `docs/model_recommendations.md`,
down from `~3-4x`. Suite green at the time (test strategy + current counts:
`docs/testing.md`).

---

## Residual gap (~2.4%, known, unfixed)

Both episodic and small. Revisit only if the gap widens past `~5%`.

1. **Failed-retry tokens.** `_invoke_with_retry` retries on 5xx and 429.
   Server-billed tokens on retried attempts are not read; only the final
   successful attempt's `usage_metadata` is logged.
2. **Schema-repair pass.** `_attempt_schema_repair` does a second
   `chain.invoke` after a parse failure. The repair `raw_result`
   overwrites the original; the failed attempt's tokens are dropped.

---

## Lessons (durable, beyond this incident)

1. **An internally-consistent metric is not a correct metric.**
   `compute_run_totals_from_llm_calls` was correct as defined — it just
   summed a wrong set of rows at wrong rates. Self-consistency is not
   validation.
2. **Pricing constants need an external reconciliation.**
   Anything where the source of truth lives outside the codebase (vendor
   pricing, regulatory thresholds, third-party API quotas) needs either
   a periodic reconciliation job or an invariant test that fails when the
   upstream changes. Constants should link to the vendor pricing page in
   a docstring, and the test should assert against the link, not against
   the constant.
3. **"Truth source" is a contract, not a label.**
   `compute_run_totals_from_llm_calls` was documented as the truth source.
   That is only true if every billable LLM call writes an `llm_calls`
   row. There was no invariant test enforcing the "every call writes one
   row" contract — RC3 and RC4 were possible and silent. Add one.
4. **Perf optimizations on one side of a seam need a paired update on the
   other.** Prompt caching was added to reduce billed input tokens. The
   cost-extraction code on the other side of the seam was not updated.
   Same shape as the field-name-drift bug noted in
   `feedback_test_invariants_for_critical_concerns.md`.
5. **Tests that encode the bug pass forever.**
   `test_estimate_cost_haiku` asserted the wrong value with a comment
   matching the wrong rate. It would never have failed. Pricing-rate
   tests need to assert against an external reference (a docstring URL,
   a periodic check, or a fixture refreshed from the live API), not
   against the constant itself.

---

## Follow-ups

- [ ] Add an invariant test: "every successful agent run produces exactly
  one `llm_calls` row with non-zero token counts." Closes the class of
  bypass that RC3 and RC4 belonged to.
- [ ] Decide whether the two residuals (failed-retry, schema-repair) are
  worth closing. Small, but they raise the floor on the gap.
- [ ] Consider a `tools/` script that compares last 7 days of
  `compute_run_totals_from_llm_calls` against an Anthropic
  usage-API call, and flags drift > 5%. This is the missing external
  reconciliation noted in lesson 1.
