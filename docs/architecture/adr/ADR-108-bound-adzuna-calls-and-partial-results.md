# ADR-108: Bound Adzuna Calls Per Run + Partial Results on Timeout

## Status

- **Accepted** (2026-06-16). Direct follow-up to ADR-107 (the client-side rate limiter),
  triggered by analyzing the first run after ADR-107 shipped.
- The limiter behaved correctly but exposed a deeper problem; this ADR fixes the volume +
  resilience gaps it surfaced.

## Context

- Post-ADR-107 run analysis (run `57626487`, 2026-06-17): **Adzuna returned 0 jobs with
  `error: "timeout"`**, while Greenhouse (164) + Lever (37) succeeded. Prior runs returned
  ~200 Adzuna jobs. A clear regression in Adzuna yield.
- **Root cause — unbounded Adzuna call volume.** The run's profile resolved to
  **19 roles x 10 physical locations + 19 remote = ~209 Adzuna API calls** (one call per
  task; no paging). That number breaks every budget:
  - At the ADR-107 limiter's 20/min, 209 calls need **~627s** — far past the **180s**
    per-scraper timeout (`_SCRAPER_TIMEOUT_S`), so the paced scrape was killed mid-flight.
  - Before ADR-107, the same 209 calls burst through 5 threads in ~40s at ~**300/min** —
    exactly what tripped the original "20/25 hits per minute" alert.
  - 209 calls also blows the ~100/day free-tier daily quota in a **single run**.
- **Two compounding bugs, not one:**
  1. **Nothing bounds Adzuna API *calls*.** The funnel caps *discovered* (50) and *scored*
     (10) jobs, but only AFTER making all 209 calls — so we pay 209 calls to keep 50 jobs.
  2. **`scrape()` is all-or-nothing.** It returns only after `as_completed` finishes, so
     the outer 180s timeout abandons the future and **all** collected Adzuna jobs are lost,
     not just the unfinished ones.

## Decision

Two coupled changes at the `ConcurrentAdzunaScraper` seam (no v1 modification).

### Part 1 — Cap calls per run (bound the daily quota + fit the timeout)

- New `scrapers.adzuna.max_calls_per_run` (`AdzunaConfig`), **default 50**. `0` = uncapped
  (legacy behavior). 50 calls at 20/min ≈ 150s, inside the 180s timeout; and 50 << the
  ~100/day quota, so several runs/day stay safe.
- When `tasks > cap`, **truncate** to the cap and **log the dropped count** (no silent
  truncation — the "no silent caps" principle).
- Select the kept subset by a **diagonal interleave** of the (title x location) grid so a
  truncated harvest **samples across many titles AND many locations**, rather than
  exhausting all titles for the first 2-3 locations (which a naive location-major order
  would do). Remote tasks are appended after the local grid.

### Part 2 — Partial results on a time budget (never lose the whole source)

- `scrape()` collects via `as_completed(futures, timeout=_SCRAPE_TIME_BUDGET_S)` where the
  budget (**150s**) sits **below** the 180s discovery timeout, and on `TimeoutError`
  **returns whatever was collected so far** (logged as partial).
- The executor is shut down with `shutdown(wait=False, cancel_futures=True)` so **queued
  (not-yet-started) tasks are cancelled** — we stop making more calls past the budget —
  while the ≤5 in-flight calls finish harmlessly in the background.
- Net: a timed-out Adzuna scrape now yields a **partial harvest** (the jobs gathered within
  budget) instead of zero, and stops spending calls once the budget is hit.

## Decision review (not a rubber-stamp)

- **Recommendation / confidence:** ship both; **high** confidence — the analysis is
  evidence-based (before/after run data) and the funnel only keeps 50/10 jobs, so a 50-call
  Adzuna budget loses no real coverage.
- **The ONE load-bearing decision: cap the call VOLUME (Part 1).** ADR-107 (rate) alone
  cannot help — 209 calls cannot be done under *any* per-minute cap within the timeout, and
  still violate the daily quota. The volume must come down. Earlier I framed "cap tasks" as
  the weakest option (ADR-107 review); the run data overturned that — at 209 calls it is
  *mandatory*, not optional. Stating the reversal explicitly.
- **Why a fixed cap + interleave over "score-then-prioritize":** we have no relevance
  signal at scrape time (scoring is downstream), so a representative *sample* (diagonal
  interleave) is the honest, cheap way to bound calls without pretending to rank.
- **Pros:** bounds per-minute AND daily AND wall-clock; never loses the whole source again;
  ~50 Adzuna calls still over-feeds a 50-job funnel; one config knob; no v1 change.
- **Cons / risks:**
  - *Coverage:* a profile with >50 title x location combos no longer queries them all in one
    run. Mitigated by the interleave (broad sampling) + the downstream 50/10 funnel caps +
    the ATS-direct sources. Raise `max_calls_per_run` (and accept slower runs / more quota)
    if a profile genuinely needs more.
  - *Partial ordering:* which calls land within budget depends on thread timing; the
    interleave makes the partial representative but not deterministic. Acceptable.
  - *Background calls:* up to `max_workers` in-flight calls complete after the budget (they
    already hold their rate slot), a bounded ≤5-call overrun. Acceptable.
- **Reversibility / cost:** `max_calls_per_run: 0` restores uncapped behavior; the budget is
  a constant. Low cost, additive config (back-compatible default).
- **Where I took the easy path:** a fixed call cap + fixed time budget rather than a
  dynamic budget derived from `(rate, timeout)`. Simpler and the defaults are mutually
  consistent (50 @ 20/min ≈ 150s < 180s); documented so a future rate/timeout change
  prompts a re-check.
- **Reasons to say NO:** "just raise the per-scraper timeout so all 209 finish." Counter:
  that makes a run take >10 min and still blows the daily quota — volume is the problem, not
  patience.

## How it integrates

- Layers on ADR-107: the limiter still paces the (now-capped) calls under the per-minute
  cap; this ADR bounds how many there are and keeps the partial harvest.
- `dependencies.py` threads `max_calls_per_run` into both Adzuna build paths (the per-run
  ADR-064 factory + the built-in).
- Honors never-lose-the-run: partial results on budget, kept-on-failure unchanged.
- ATS-direct (Greenhouse/Lever/Workday) untouched — source-of-truth feeds with no per-minute
  cap and full listings.

## Out of scope

- Relevance-ranked task selection (no signal at scrape time).
- Dynamic time budget derived from the live rate/timeout (fixed constants for now).
- Daily-quota accounting/persistence (the per-run call cap bounds it indirectly).

## PSSR

- **Performance/Scalability:** caps Adzuna wall-clock at the time budget; fewer calls = less
  network + faster discovery; partial results avoid wasted full-scrape work.
- **Security:** no new surface.
- **Reliability:** the core win — a slow/large Adzuna scrape degrades to a partial harvest
  instead of zero, and call volume is bounded so the daily quota is not exhausted in one run.

## Tests

- Cap: `tasks > max_calls_per_run` truncates to the cap and logs the dropped count;
  `max_calls_per_run=0` submits all tasks.
- Interleave: the kept prefix spans multiple distinct titles AND locations (not all titles
  for one location).
- Partial results: with a slow fake `_fetch_jobs` and a tiny time budget, `scrape()` returns
  the jobs collected within budget and does not block on the remaining tasks.

## Addendum (2026-06-16): per-run rotation for tail coverage

**Problem the base ADR left open.** The call cap is *deterministic* — `_interleave_tasks`
produces the same order every run and `scrape()` takes the same `tasks[:cap]` prefix. So
the dropped title x location combinations (the ~159 beyond the cap) are dropped **every**
run, never queried. The interleave guarantees every *title* and every *location* appears in
the kept prefix, but specific *pairings* in the tail stay permanently dark.

**Decision.** Rotate the kept window per run so successive runs query *different* slices of
the grid, giving **eventually-complete coverage** across runs.

- `ConcurrentAdzunaScraper` gains a `rotation_seed` (default 0). When `tasks > cap`, it
  rotates the interleaved list by `offset = (rotation_seed * cap) % total` before taking the
  `cap` prefix. Consecutive seeds therefore walk consecutive slices; after
  `ceil(total / cap)` runs the whole grid is covered, then it wraps.
- The seed is a **monotonic per-profile run counter**: `WorkflowRepository.count_for_user`
  (minus the current run). The Adzuna factory (`dependencies.py`), which closes over
  `workflow_repo`, computes it from the run's `user_id` (threaded in from `discover_jobs`),
  so it **survives process restarts** (the user restarts between runs) — an in-memory
  counter would reset to 0 and never rotate.
- Rotation only engages when `total > cap`; otherwise all tasks run and the seed is moot.
  `rotation_seed=0` reproduces the base ADR-108 behavior (slice from 0).

**Honest limits (best-effort, not strict round-robin).**
- The seed counts **all** of a profile's runs, so non-search runs advance it too — slices
  can be skipped within a cycle but are caught on the wrap. Coverage is "eventually
  complete," not "complete in exactly `ceil(total/cap)` runs."
- If the title/location grid changes between runs (config edit), `total` changes and the
  slice math reshuffles — still rotating, still broadly covering.
- Adzuna sorts by date, so each slice returns *fresh* postings regardless; rotation's value
  is reaching tail *pairings* a fixed prefix never would.

**Tests added:** consecutive seeds select disjoint, advancing windows; the union over
`ceil(total/cap)` seeds covers the full grid; `rotation_seed=0` == base behavior.

## References

- ADR-107 (rate limiter), `app/services/concurrent_adzuna_scraper.py`,
  `app/services/job_discovery_service.py` (`_SCRAPER_TIMEOUT_S`), `models/config_schema.py`
  (`AdzunaConfig`), `app/api/dependencies.py`, `app/workflows/nodes/discover_jobs.py`,
  `app/repositories/workflow_repository.py` (`count_for_user`), run analysis `57626487`
  (2026-06-17).
