# ADR-081: ATS-Direct Job Sources (Greenhouse + Lever) — Prototype

## Status

Accepted (2026-06-04). Implemented as a prototype/spike outcome.

Follows the `spike_job_data_sources.md` recommendation and is the root-cause
response to the Adzuna dead-link problem that ADR-080 patched with a staleness
proxy.

## Context

ADR-080 mitigated Adzuna's "renders but the apply link is dead" problem with a
posting-age signal, but age is a proxy — the link is still unverified, and Adzuna
429-blocks any server-side verification. The root cause is that Adzuna is an
**aggregator**: it is one step removed from the employer, so its index lags reality.

The spike (`spike_job_data_sources.md`) identified **ATS-direct** feeds as the
structural fix: an employer's own Applicant Tracking System board only returns
**currently published** postings, and the apply URL is the employer's own
ATS-hosted page — so dead links do not arise, and these endpoints are not
bot-blocked. Both candidate APIs were verified live (2026-06-04): no auth, HTTP
200, full JD, real apply URL, no 429.

The tradeoff: ATS boards are queried **per company** (by board token / slug), so
they need a curated company list rather than a market-wide keyword search.

## Decision

Add two ATS-direct scrapers, **off until a profile lists target companies**, run
**alongside** Adzuna (purely additive). They reuse the existing scraper seam, so
`JobDiscoveryService` normalizes + dedups them like any other source.

### A. Scrapers (`app/services/ats_scrapers.py`)

`GreenhouseScraper` and `LeverScraper` implement the v1 `BaseScraper.scrape() ->
list[Job]`. Verified field mappings (2026-06-04):

- **Greenhouse** `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`:
  `absolute_url -> url`, `title`, `company_name -> company`, `location.name`,
  `first_published|updated_at -> posted_at`, `content` (HTML) `-> description`
  (entities unescaped, tags stripped).
- **Lever** `api.lever.co/v0/postings/{slug}?mode=json`: `text -> title`,
  `hostedUrl -> url`, `categories.location`, `createdAt` (epoch ms) `-> posted_at`,
  `descriptionPlain -> description`; company = the slug.

Both feed ADR-080's `posted_at` for free. A board can list hundreds of roles, so
each is bounded (`_MAX_JOBS_PER_BOARD`) and gated by **title relevance derived from
the run's roles** (reusing `relevance_tokens`, ADR-064) plus `EXCLUDED_TITLE_KEYWORDS`.
Per-board fetch failures are logged and skipped, never fatal.

### B. Wiring (per-run factory, ADR-064 shape)

`build_ats_scrapers(roles, scrapers_cfg)` returns the configured scrapers (or `[]`).
A `WorkflowDependencies.ats_scraper_factory(roles) -> list` closure is built in
`_build_real_deps` from `config.scrapers.{greenhouse,lever}.companies` and threaded
into `discover_jobs`, which extends `extra_scrapers` with the result. New
`JobSource.GREENHOUSE` / `LEVER` (v1 + v2 enums + `_SOURCE_MAP`).

### C. Source-enum cleanup

Removed the unused `GLASSDOOR` / `LADDERS` source values and the vestigial
`LaddersConfig` (no scraper since ADR-063; not referenced in app code). `INDEED` is
kept — Adzuna results map to it.

## Options considered

- **Headless-browser link-verification of Adzuna** (ADR-080 reject) — heavy,
  brittle, still blocked. ATS-direct removes the need entirely.
- **Another aggregator** (Jooble/JSearch) — same staleness failure mode.
- **ATS-direct, per-company, opt-in (chosen)** — live links, full JDs, no 429;
  cost is curating a company list.

## Consequences

### Positive

- For listed companies: live apply links, full structured JDs, fresh `posted_at` —
  the dead-link problem does not occur. Additive; Adzuna keeps the broad net.
- ATS apply URLs are stable (no rotating session token), so URL dedup is *more*
  reliable than with Adzuna.

### Tradeoffs / prototype limits (follow-ups)

- Needs a curated company->token list (manual config today). Sourcing/maintaining
  it at scale is open (a registry bootstrap is a follow-up).
- Per-run fan-out across N boards adds latency (bounded by the existing 180s
  scraper timeout + `_MAX_JOBS_PER_BOARD`); not yet concurrency-tuned.
- Location is not filtered server-side (ATS returns all; location is per-job).
- `OpenAIProvider`-style breadth tuning, demote-vs-keep-Adzuna, and ATS<->Adzuna
  cross-source dedup quality are left to measure.

### Neutral

- Docs (architecture-docs sweep): ADR-081 + index, `data_model.md` (source values),
  `config_model.md` / `config.example.yaml` (the company lists), `workflow_model.md`,
  `architecture_overview.md`, `agent_graph_overview.md` (sources), CLAUDE.md scraper
  rules + shared-libraries note, `wiki.md`, CHANGELOG, and `spike_job_data_sources.md`
  (status -> prototyped). Tests: field mapping (mocked API JSON), relevance/exclusion
  gating, graceful per-board failure, `JobSource` normalization.

## References

- `spike_job_data_sources.md` — the research this implements.
- ADR-080 — the posting-age staleness patch this complements (still useful for
  Adzuna results).
- ADR-064 — per-run role-derived relevance (reused).
- ADR-063 — v1 retirement (why `LaddersConfig` was vestigial).
