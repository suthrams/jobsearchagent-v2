# ADR-097: Curated, Live-Verified ATS-Direct Board Batch (Greenhouse + Lever)

## Status

**Accepted** (2026-06-10). Implemented same day (full batch active + concurrency).

Builds directly on ADR-081 (ATS-direct scrapers, prototype) and follows
`spike_job_data_sources.md` (source-of-truth feeds over aggregators). No new
scraper mechanism is introduced; this ADR is about **populating, verifying, and
maintaining the curated company list** that ADR-081 left empty, and deciding the
default posture (opt-in vs shipped-on).

## Context

ADR-081 added `GreenhouseScraper` + `LeverScraper` and the per-run
`ats_scraper_factory`, but shipped with **empty** `scrapers.{greenhouse,lever}.companies`
lists — so ATS-direct discovery is dormant until someone hand-curates board tokens.
ADR-081 explicitly named "sourcing/maintaining the company list" as the open
follow-up.

A request to source jobs from `jobright.ai` was investigated and **rejected** (see
the new "Rejected source" note in `spike_job_data_sources.md`): it has no public
API, its `/api/` is `robots.txt`-disallowed and `/jobs/` is blocked for our bots,
and its only open surface (curated GitHub repos) is unlicensed, format-unstable,
and links only to `jobright.ai` redirect URLs rather than the employer's own apply
page. It fails the "reliable, trustworthy, no security risk" bar. Crucially, the
roles jobright aggregates are overwhelmingly **Greenhouse/Lever/company-ATS**
postings — so the trustworthy way to get "the same jobs" is to go ATS-direct from
the source, which we already support.

This ADR curates that batch.

## Decision

Populate the ATS company lists with a **batch of reputable tech employers whose
boards were verified live on 2026-06-10**, and adopt a standing **verify-before-add**
rule so the list stays reliable.

### A. Verification standard (reliability gate)

A board token/slug is eligible **only if** a live call returns HTTP 200 and a
non-empty job array:

- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs`
- Lever: `GET https://api.lever.co/v0/postings/{slug}?mode=json`

Verification was run 2026-06-10 against ~65 candidates; only boards returning jobs
are included. A committed, reusable checker (`tools/verify_ats_boards.py`) will be
added so the batch can be re-verified periodically (boards rename/disappear), and
dead boards pruned. Per-board fetch failures are already non-fatal at runtime
(logged + skipped in `ats_scrapers.py`), so a board going stale degrades gracefully
rather than failing a run.

### B. The verified batch (2026-06-10)

**Greenhouse (33):** affirm, airbnb, anthropic, asana, attentive, brex, chime,
cloudflare, coinbase, databricks, datadog, discord, dropbox, elastic, faire, figma,
flexport, gitlab, gusto, hightouch, instacart, mercury, pinterest, reddit,
robinhood, samsara, scaleai, sofi, stripe, twilio, upgrade, vercel, verkada.

**Lever (3):** gopuff, palantir, spotify. (`ledger` = 1 posting and `leverdemo` =
demo board were dropped.)

All are reputable employers that post senior / staff / principal / architect /
engineering-manager roles, matching the system's senior-tuned profile.

### C. Default posture — DECIDED: shipped-on, full batch

Three options were weighed, in order of how aggressively ATS-direct turns on:

1. **Example-only (most conservative).** Put the batch in `config.example.yaml`
   only; leave the active `config.yaml` empty. Behaviour unchanged until a user
   copies it in. (Effectively "documented but still opt-in".)
2. **Shipped-on, curated subset (recommended).** Ship the full batch in
   `config.example.yaml`, and activate a **sensible default subset (~12-15 boards)**
   in `config.yaml` so ATS-direct contributes out of the box without a 36-board
   per-run fan-out. Users add more from the documented full list.
3. **Shipped-on, full batch.** Activate all 36 boards in `config.yaml`.

**Decided: Option 3** — all 33 Greenhouse + 3 Lever boards are active in
`config.yaml`, and the same batch is documented in `config.example.yaml`. Made
viable by the concurrency decision (D), which keeps a 36-board fan-out to a few
seconds. `enabled: true` stays the default for both ATS sources, so any user can
switch them off without editing the lists.

### D. Performance — DECIDED: concurrency added

Previously `GreenhouseScraper.scrape()` / `LeverScraper.scrape()` fetched boards
**sequentially** (a `for` loop over tokens): 36 boards x ~0.3-0.6s each is ~15-20s
added to discovery — inside the existing 180s per-scraper timeout, but noticeable.
ADR-081 flagged "not yet concurrency-tuned" as a follow-up.

**Decided: added now.** The per-board fetch is parallelized with a bounded
`ThreadPoolExecutor` (`_DEFAULT_WORKERS = 8`, mirroring `ConcurrentAdzunaScraper`),
collecting results in token/slug order so output stays deterministic and the
existing mocked-API tests keep passing. Per-board failures remain non-fatal
(logged + skipped). Contained to `ats_scrapers.py`; resolves the ADR-081 follow-up.

## How it integrates (no new mechanism)

### Into the scraper layer

`config.scrapers.{greenhouse,lever}.companies` -> `_ats_factory` closure in
`app/api/dependencies.py` (`_build_real_deps`) -> `WorkflowDependencies.ats_scraper_factory`
-> `build_ats_scrapers(roles, scrapers_cfg)` builds `GreenhouseScraper` /
`LeverScraper` for the run's roles. Each board response is bounded by
`_MAX_JOBS_PER_BOARD = 100` and gated by **role-derived title relevance**
(`relevance_tokens`, ADR-064) plus `EXCLUDED_TITLE_KEYWORDS`, so a 783-role board
(databricks) cannot flood a run. Scrapers implement `BaseScraper.scrape() ->
list[Job]`, so they are indistinguishable downstream from Adzuna.

### Into the discovery node and funnel

In `discover_jobs` (`app/workflows/nodes/discover_jobs.py:83-87`) the factory's
scrapers are appended to `extra_scrapers`, then `JobDiscoveryService.discover_with_stats`
runs the unified pipeline for every source identically:

1. **Normalize + per-user URL dedup** (drops URLs this user already scored). ATS
   apply URLs are stable (no rotating session token), so dedup is *more* reliable
   than Adzuna's.
2. **Posting-age filter** (ADR-080) — ATS `posted_at` is real, so this works well.
3. **Dead-link filter** (ADR-095, opt-in) — ATS links are live by construction, so
   this rarely drops an ATS job (it mainly protects Adzuna).
4. **Node cap** `get_max_discovered_jobs(state)` (<=50) bounds the merged set.

From there the standard graph is unchanged: `load_resume` -> optional
`relevance_filter` (ADR-079) -> `score_jobs` (<= `scoring.max_scored`, <=25) ->
`auto_select` -> `deep_review` -> `career_advice` -> `generate_report`, with
out-of-graph on-demand ops available per scored job (ADR-055/061). Senior tuning is
unchanged and stays governed by `scoring.min_match_score`. ATS jobs are purely
additive alongside Adzuna; nothing about the existing flow changes shape.

## Options considered

- **Integrate jobright.ai** (the original request) — rejected: no API, ToS/robots
  disallow, unlicensed + unstable GitHub surface, redirect-only links. Fails the
  reliability/trust/security bar. Documented in `spike_job_data_sources.md`.
- **Another aggregator** (Jooble/JSearch/RapidAPI) — same staleness + trust issues
  ADR-080/081 already rejected; many are paid/keyed.
- **Curated ATS-direct batch (chosen)** — source-of-truth, live links, full JDs,
  no auth, no 429; cost is curating + maintaining the list, addressed by the
  verify-before-add rule and the reusable checker.

## Consequences

### Positive

- ATS-direct stops being dormant: the system gets live, full-JD, source-of-truth
  postings from ~36 strong employers, with stable apply URLs and real `posted_at`.
- Trust/security: only official, unauthenticated employer ATS endpoints; apply URLs
  are the employer's own page (no third-party redirector). Job descriptions remain
  untrusted input per ADR-019 (already handled).
- Reliable by construction: every board is live-verified, and per-board failures
  degrade gracefully.

### Tradeoffs

- Per-run latency grows with list size (the Performance decision point above).
- The list is a maintenance surface — boards rename/close; the reusable checker +
  periodic re-verification mitigate this. Point-in-time verified 2026-06-10.
- Coverage is employer-by-employer, not market-wide; Adzuna keeps the broad net.

### PSSR

- **Performance:** 36 board GETs now run concurrently (D), a few seconds vs ~15-20s.
- **Scalability:** fan-out is linear in list size; `_MAX_JOBS_PER_BOARD` + node cap
  bound the downstream volume regardless.
- **Security:** public read-only APIs, no secrets, employer-hosted apply URLs; no
  new trust boundary beyond the already-untrusted JD text.
- **Reliability:** non-fatal per-board failures; live-verified list; graceful
  staleness handling.

### Docs + tests (at implementation, after approval)

Architecture-docs sweep: this ADR + ADR index, `config_model.md` +
`config.example.yaml` (the batch + verify-before-add note), `spike_job_data_sources.md`
(jobright rejection + status -> curated/active), `workflow_model.md` /
`architecture_overview.md` / `agent_graph_overview.md` (sources mention),
CLAUDE.md scraper rules, `wiki.md`, CHANGELOG. Tests: `tools/verify_ats_boards.py`
present + a unit test that the shipped example list is well-formed (lowercase, no
dupes, non-empty); the existing mocked-API mapping/relevance/failure tests already
cover scraper behaviour.

## References

- ADR-081 — ATS-direct scrapers (the mechanism this populates).
- ADR-080 — posting-age staleness (complemented; ATS `posted_at` is real).
- ADR-064 — per-run role-derived relevance (reused for the title gate).
- ADR-079 / ADR-095 — relevance pre-filter / dead-link filter in the funnel.
- `spike_job_data_sources.md` — aggregator-vs-source-of-truth research + the
  jobright.ai rejection.
