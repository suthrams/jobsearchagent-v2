# ADR-107: Client-Side Rate Limiter for Adzuna (Stay Under the Per-Minute Cap)

## Status

- **Accepted** (2026-06-16). Triggered by a live Adzuna alert: "Hits per minute:
  20/25 (>= 80%)".
- Touches the retained v1 Adzuna scraper boundary (ADR-050/063) via the v2
  `ConcurrentAdzunaScraper` wrapper — **no v1 modification** (subclass/wrap only).

## Context

- Adzuna's free tier enforces a **per-minute** hits cap (the alert shows `X/25`).
  Separate from the documented daily quota guard
  (`len(titles)*len(locations)+len(remote_keywords) < 100/day`).
- Discovery makes **one API call per task** (no paging — the endpoint is the fixed
  `search/1`), so `calls == tasks == locations*titles + remote_keywords`. Realistic
  per-run task counts are ~10-25.
- `ConcurrentAdzunaScraper` fans those tasks out across **5 worker threads with no rate
  governor** (`app/services/concurrent_adzuna_scraper.py`), so ~20 calls burst out within
  seconds -> 80% of the 25/min cap on a normal run, and a larger role/location set crosses
  it -> `429`s.
- A `429` is then made **worse** by the v1 `@retry` (`wait_exponential`, 3 attempts) which
  does **not** honor `Retry-After` and adds more calls; timed-out fetches lose jobs.
- This is a **general** reliability gap, not profile-specific: any profile with enough
  title x location combinations hits it. The fix belongs at the scraper seam.

## Decision

Add a **process-global, client-side rate limiter** that paces Adzuna call-starts to stay
under the per-minute cap, and back it off on an observed `429`.

- New `app/services/rate_limiter.py`:
  - `RateLimiter(max_per_minute)` — thread-safe **min-interval** limiter
    (`interval = 60 / max_per_minute`). `acquire()` blocks until the next evenly-spaced
    slot, so call-starts never exceed `max_per_minute` in a rolling minute (no burst).
    `penalize(seconds)` pushes the next slot out (honor-429). `tighten(max_per_minute)`
    only ever *lowers* the rate (never loosens).
  - `get_adzuna_limiter(max_per_minute)` — returns a **module-global singleton** so all
    Adzuna scraper instances in the process (incl. concurrent runs) share ONE budget;
    `max_per_minute <= 0` disables (returns `None`).
- `ConcurrentAdzunaScraper`:
  - `make(..., max_calls_per_minute=...)` resolves the shared limiter and stores it.
  - `scrape()` calls `limiter.acquire()` **inside each worker task**, right before
    `s._fetch_jobs(...)`, so the 5 workers' call-starts are serialized to the budget
    while still overlapping HTTP response latency.
  - On a task failure, if it is a `429`, call `limiter.penalize(Retry-After or default)`
    so the remaining tasks slow down (honor-429 at the wrapper).
- Config: new `scrapers.adzuna.max_calls_per_minute` (`AdzunaConfig`), **default 20** —
  safely under the 25/min cap with margin. `0` disables the limiter.
- Wiring: `dependencies.py` passes `max_calls_per_minute` into both Adzuna build paths
  (the per-run `_adzuna_factory`, ADR-064, and the built-in `_build_scrapers`).

## Decision review (not a rubber-stamp)

- **Recommendation / confidence:** ship it; **high** confidence it removes the reported
  alert and prevents `429`s for realistic task counts.
- **The ONE load-bearing decision: bound the RATE (limiter) vs the alternatives.** Chosen
  the limiter because it is the only option that *guarantees* staying under a per-minute
  cap regardless of task or worker count. Reducing workers only softens the burst (enough
  tasks still cross the cap); capping tasks bounds the total, not the rate, and silently
  drops coverage. (User-confirmed 2026-06-16.)
- **Second decision: even-spacing (min-interval) vs burst-then-throttle (sliding window).**
  Chose min-interval — lowest peak rate, simplest to reason about, and the provider is
  already warning us, so the conservative shape is the right default. Cost: small runs are
  paced too (a 5-task run takes ~12s instead of ~3s); acceptable inside a multi-minute run.
- **Pros:** one small module + a wrapper change fixes a real production alert generally;
  process-global so concurrent runs can't collectively breach the cap; configurable + can
  be disabled; no v1 modification.
- **Cons / risks (estimated):**
  - *Latency:* a run is paced at `>= tasks * (60/max)` seconds for the Adzuna stage. At
    20/min, 50 tasks = 150s, under the 180s `_SCRAPER_TIMEOUT_S`; **>60 tasks could exceed
    it** -> partial Adzuna results (kept, never a crash — `shutdown(wait=False)`). Realistic
    counts (<=25) are far under. Documented, not mitigated further.
  - *Honor-429 is best-effort:* the limiter backs off after a task *returns* a 429, but the
    v1 `@retry`'s own inner attempts still fire without reading `Retry-After` (gating those
    would require modifying v1, which the subclass-only boundary forbids). Acceptable
    because the limiter's whole job is to keep us from reaching a 429 in the first place.
  - *Global-rate under differing configs:* if two concurrent runs request different
    `max_calls_per_minute`, the singleton adopts the stricter (lower) — never the looser —
    so the cap is always respected.
- **Reversibility / cost:** trivially reversible — set `max_calls_per_minute: 0`. Low cost,
  additive config field (back-compatible default).
- **Where I took the easy path:** wrapper-level honor-429 rather than threading
  `Retry-After` through v1's tenacity retry; chose min-interval over a token bucket to
  avoid a second "burst" knob. Both noted above.
- **Reasons to say NO:** "just raise the daily-quota awareness / lower workers." Counter:
  neither bounds the *per-minute* rate, which is the cap that actually alerted. "It slows
  discovery." Counter: discovery latency is dwarfed by LLM scoring, and the limiter is
  configurable/disengageable.

## How it integrates

- Purely additive alongside the ATS-direct sources (Greenhouse/Lever/Workday), which are
  source-of-truth feeds with no 429 issue and are **not** rate-limited by this ADR.
- The limiter is engaged only when the scraper has an Adzuna config with
  `max_calls_per_minute > 0` (the default), so existing behavior is unchanged except for
  the pacing.
- Honors the never-lose-the-run contract: a limiter is best-effort; a timed-out Adzuna
  stage still returns whatever completed.

## Out of scope

- Rate-limiting the ATS-direct / Workday / custom-URL scrapers (different providers, no
  per-minute cap observed). The `RateLimiter` is reusable if one later needs it.
- Honoring `Retry-After` inside v1's `_fetch_jobs` retry (would require modifying v1).
- A daily-quota governor (the existing `< 100/day` guidance + task count already bound it).

## PSSR

- **Performance/Scalability:** adds bounded sleep to the Adzuna stage only; no extra
  calls, no DB/LLM cost. Stays within the per-scraper timeout for realistic task counts.
- **Security:** no new surface; no secret handling.
- **Reliability:** the core win — prevents `429`s and the retry storms / lost jobs they
  cause; process-global so the guarantee holds under concurrent runs; default-on,
  disengageable.

## Tests

- `RateLimiter`: even spacing under a mocked monotonic clock; `acquire()` returns the
  waited duration; `penalize()` pushes the next slot out; `tighten()` only lowers the
  rate; `max_per_minute=0` disables.
- `get_adzuna_limiter`: returns a shared singleton; a later stricter request tightens it;
  `<=0` returns `None`.
- `ConcurrentAdzunaScraper`: `acquire()` is invoked once per task; a simulated `429` task
  triggers `penalize`.

## References

- `app/services/rate_limiter.py`, `app/services/concurrent_adzuna_scraper.py`,
  `app/api/dependencies.py` (both Adzuna build paths), `models/config_schema.py`
  (`AdzunaConfig`), ADR-050 (concurrent wrapper), ADR-064 (per-run Adzuna),
  `docs/architecture/spike_job_data_sources.md`.
