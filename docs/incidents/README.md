# Incidents log

This directory captures critical issues that affected production behavior or
load-bearing system invariants, plus the root cause and fix. Each incident is
one file: `docs/incidents/YYYY-MM-DD-short-slug.md`.

## When to add an entry

Open a new file when **any** of these are true:

- A user-visible system promise (cost, security, correctness, persistence) was
  violated and the violation was not caught by the test suite.
- A bug reached a real run because no invariant test enforced the contract.
- Two correct changes interacted across an un-owned seam to produce a wrong
  result (perf optimization that broke a measurement, schema migration that
  drifted a field name, etc.).
- A pricing, quota, or vendor-rate constant in the codebase diverged from
  ground truth.

Skip for: typical bugs caught by tests, dependency bumps, formatting, ordinary
refactors. The bar is "the system promised X and delivered not-X."

## Entry shape

Each file follows the structure of
[`2026-05-07-cost-tracking-undercount.md`](2026-05-07-cost-tracking-undercount.md).
Required sections:

1. **Headline** — one sentence: what was broken, by how much.
2. **Symptom** — what the user saw.
3. **Root causes** — numbered list. Each cause has *what / how it got there /
   magnitude*. Independent causes get independent numbers, even if they
   compounded into one symptom.
4. **Why it was silent** — why tests, docs, or dashboards did not flag it.
   This is the most useful section for the next incident.
5. **Fixes** — table mapping each root cause to the change that closed it,
   linked to the commit / PR.
6. **Validation** — empirical evidence the fix worked (numbers, not "the
   suite passed").
7. **Residuals** — known remaining gaps and the threshold for revisiting.
8. **Lessons** — durable patterns that generalize beyond this incident.

Keep entries tight. The audience is future-you under time pressure, not a
review board.

## Index

| Date | Incident | Severity | Commit |
|---|---|---|---|
| 2026-05-07 | [Cost-tracking undercount (~3-4x)](2026-05-07-cost-tracking-undercount.md) | High (budget projections wrong by a factor) | [`6cb0048`](https://github.com/suthrams/jobsearchagent-v2/commit/6cb00483dfa1388d4de9fb91f7aad327567963b7) |

## Related

- `docs/cost_troubleshooting.md` — operational runbook for cost surprises.
- Memory: `feedback_test_invariants_for_critical_concerns.md` — the rule that
  gets violated most often in this codebase. Most incidents here will reference
  it.
