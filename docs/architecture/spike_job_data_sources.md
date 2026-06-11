# Spike: Free job-data API alternatives to Adzuna

Status: SPIKE (research). 2026-06-04. Companion to ADR-080 (the near-term
posting-age patch). The recommendation below (Greenhouse + Lever, ATS-direct) was
**prototyped in ADR-081** (`app/services/ats_scrapers.py`) and a curated,
live-verified board batch was **turned on by default in ADR-097** (2026-06-10).

> Source-terms caveat: API terms, free-tier limits, and coverage change. Endpoints
> and shapes below reflect research as of 2026-06; **verify current terms against
> each provider's official docs before building.** This is a decision-support
> spike, not a final design.

## 1. Problem

Adzuna's dead-link problem is structural to being an **aggregator**: it indexes
other sites, so its copy lags reality (the employer pulls the req; Adzuna's listing
lingers; "renders but apply is dead"). It also 429-blocks server-side fetches
(`Retry-After: 3600`), and its apply link is a tracking redirect to an
employer/ATS page we cannot verify. Swapping one aggregator for another inherits
the same failure mode. The structural fix is to query **source-of-truth** feeds:
the employer's own Applicant Tracking System (ATS), which only returns currently
published postings and whose apply URL is the employer's own ATS-hosted page.

## 2. Evaluation criteria

Auth model; free-tier limits; coverage (geo + roles); data structure/quality (full
JD? real apply URL?); **source-of-truth vs aggregator** (dead-link resistance — the
core concern); and integration effort into this repo's pluggable scraper seam
(`scrapers/base.py` -> `JobDiscoveryService.normalize` -> `JobPosting`, wired in
`dependencies.py::_build_scrapers` / per-run factories).

## 3. ATS-direct sources (source-of-truth — the recommended direction)

| Source | Auth | Endpoint (verify) | JD | Apply URL | Coverage | Dead-link resistance |
|---|---|---|---|---|---|---|
| **Greenhouse** | none (read) | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | Full JD (`content`) | employer-hosted `absolute_url` | Large US/tech (many startups + scale-ups) | High (live feed) |
| **Lever** | none (read) | `api.lever.co/v0/postings/{company}?mode=json` | `descriptionPlain` (no HTML strip) | ATS-hosted `hostedUrl` | US startup/scale-up | High |
| Ashby / Workable / SmartRecruiters / Recruitee | none–key (varies) | per-provider public board | varies | ATS-hosted | Smaller, additive | High |

ATS feeds are queried **per company** (by board token / slug), so they need a
**curated target-company list**. Two seam wins: ATS apply URLs are stable (no
rotating session token, so URL dedup gets *more* reliable), and ATS endpoints
rarely 429.

### 3a. Curated batch wired on (ADR-097, 2026-06-10)

The ATS-direct mechanism (ADR-081) shipped with empty company lists. ADR-097
curated a **live-verified batch** — 33 Greenhouse + 3 Lever boards, each confirmed
returning jobs on 2026-06-10 — and turned it on by default in `config.yaml` +
`config.example.yaml`, with per-board fetch parallelized (`ThreadPoolExecutor`).
Re-verify / prune with `python tools/verify_ats_boards.py` (boards rename or close
over time; per-board failures are non-fatal at runtime).

### 3b. Rejected source — jobright.ai (2026-06-10)

Investigated on request; **rejected**. jobright.ai is a consumer "AI job-search
copilot", not a data provider: no public API; its `/api/` is `robots.txt`-disallowed
and `/jobs/` is blocked for our bots; the personalized feed is login-gated. Its only
open surface is curated GitHub repos (e.g. `jobright-ai/2026-Software-Engineer-New-Grad`)
which are **unlicensed**, format-unstable (markdown tables, rolling 7-day window),
and whose apply links are `jobright.ai` **redirect** URLs, not the employer's own
page. It fails the reliability / trust / no-security-risk bar. The roles it
aggregates are overwhelmingly Greenhouse/Lever/company-ATS postings anyway, so the
source-of-truth path (ADR-097) gets the same jobs cleanly.

## 4. Aggregators & niche boards (most share Adzuna's staleness)

| Source | Auth | Notes | Source-of-truth? |
|---|---|---|---|
| JSearch (RapidAPI) | key (freemium) | Wraps Google-for-Jobs; broad | No (aggregator) |
| Jooble | key (free on request) | Broad aggregator | No |
| **USAJobs** | key (free) | US **federal** only; fully structured | Yes (gov source) |
| **Arbeitnow** | none | ATS-sourced -> fresher; good cheap EU/remote breadth | Mostly (ATS-fed) |
| Remotive | none/key | Remote; **24h delay + attribution ToS** to respect | Partial |
| RemoteOK / Jobicy | none | Remote/tech; small | Partial |
| The Muse | key (free tier) | Curated company listings + company context | Partial |
| Reed (UK) / Careerjet | key | Geo-specific aggregators | No |

## 5. Integration effort against this repo (small)

A new ATS scraper implements the v1 `BaseScraper.scrape() -> list[Job]` (one
method), mapping ATS fields into the v1 `Job` that `JobDiscoveryService.normalize`
already turns into `JobPosting`:

- Greenhouse: `absolute_url` -> `url`, `content` -> `description`, `updated_at` ->
  `posted_at` (feeds ADR-080's age signal for free), `title`, company from board.
- Lever: `hostedUrl` -> `url`, `descriptionPlain` -> `description`, `createdAt` ->
  `posted_at`.

Wiring is ~10 lines in `dependencies.py` via the existing per-run factory +
`extra_scrapers` path (copy the `_adzuna_factory` / `custom_url_factory` pattern);
the discovery seam already merges + dedups across multiple scrapers, so a new source
runs **alongside** Adzuna at first. One additive change worth flagging: add
`greenhouse` / `lever` members to the `JobSource` enum + `_SOURCE_MAP`
(`job_discovery_service.py`), else they fall back to `MANUAL`.

## 6. Recommendation

Prototype, in order, **as supplements alongside Adzuna** (then measure whether to
demote Adzuna):

1. **Greenhouse** — no-auth reads, largest US/tech coverage, `content=true` returns
   the full JD, employer-hosted apply URL. Highest-leverage root-cause fix.
2. **Lever** — also no-auth source-of-truth; `descriptionPlain` needs no HTML->text
   step. Greenhouse + Lever together cover most US startup/scale-up postings.

**The tradeoff:** ATS feeds are per-company, so they need a curated company/board
list. Recommended start: a hand-curated YAML of 50-200 target companies
(deterministic, free, and itself a quality filter), grown later via offline
slug-probing or a registry bootstrap. Narrower net, but **live links + full JDs** —
the right trade for a targeted senior search.

## 7. Open questions for a follow-up prototype

- How to source/maintain the company->board-token list (manual YAML first; can it
  be bootstrapped from a public ATS registry?).
- Per-run quota/latency of fanning out across N company feeds vs Adzuna's keyword
  search; concurrency + the existing 180s scraper timeout.
- Dedup across ATS + Adzuna when the same job appears in both (URL canonicalization
  already helps; ATS URLs are more stable).
- Whether to demote or keep Adzuna for breadth once ATS coverage is in.

## 8. Sources

Research pass (2026-06) via the spike agent: 12 web searches across the official API
docs for Greenhouse (`developers.greenhouse.io` job-board API), Lever
(`github.com/lever/postings-api`), USAJobs (`developer.usajobs.gov`), Arbeitnow,
Remotive, The Muse, RemoteOK, Jobicy, Reed, Careerjet, JSearch/RapidAPI, and Jooble.
**Verify each provider's current terms and rate limits before building** — the
table values are research-time snapshots, not guarantees.
