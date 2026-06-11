# Spike: Workday ATS-direct source (de-risk before building)

Status: SPIKE (live-API research). 2026-06-11. Companion to BUG-010 (truncated
aggregator snippets defeat the clearance + experience filters) and to ADR-081 (the
Greenhouse/Lever ATS-direct pattern this would extend). Decision-support only — the
scraper itself is proposed separately (ADR-101, pending).

> Caveat: the Workday CXS API is **undocumented**. Shapes and behavior below reflect
> live probes on 2026-06-11 and can change without notice. Re-verify before building,
> and treat the API as best-effort (never-lose-the-run), like the other scrapers.

## 1. Why

BUG-010 root cause: Adzuna (an aggregator) stores only a ~500-char JD snippet, so the
clearance sentence and the "N years" requirement are frequently truncated away and the
deterministic filters never see them. The cleared-government employers that BUG-010
cares about (Booz Allen, Leidos, CACI, GDIT, ...) post on **Workday**, not on the
Greenhouse/Lever feeds ADR-081 already supports. ADR-081's source-of-truth thesis is
right; Workday is where the missing employers live. This spike asks the load-bearing
questions before committing to a scraper: **does the Workday feed expose full JDs over
a stable API, and does it block automated reads?**

## 2. The contract (confirmed)

Workday career sites (`{tenant}.{dc}.myworkdayjobs.com/{site}`) expose an undocumented
but consistent JSON "CXS" API. Two calls:

- **List:** `POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`
  with body `{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}`
  -> `{ total, jobPostings: [{ title, externalPath, locationsText, postedOn, bulletFields }] }`.
  Paginates via `offset`/`limit`; `searchText` filters server-side.
- **Detail (full JD):** `GET https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}`
  -> `{ jobPostingInfo: { jobDescription (HTML), ... } }`.

A board is a **3-part id**: `tenant` + datacenter (`wd1`/`wd5`/`wd103`/...) + `site`.
Only a `User-Agent` header was needed; no auth, no cookie, no token.

## 3. Method

A throwaway probe (httpx) hit the list endpoint once + one detail fetch per board,
across 4 defense contractors + 2 commercial tenants. Board ids were found from each
employer's public career URL via web search.

## 4. Results — 6/6 clean, zero blocks

| Board | tenant.dc/site | Type | list | total | detail JD chars | clearance word in JD |
|---|---|---|---|---|---|---|
| Booz Allen | `bah.wd1/BAH_Jobs` | defense | 200 | 1,906 | 6,683 | yes |
| Leidos | `leidos.wd5/External` | defense | 200 | 2,000 | 8,652 | yes |
| CACI | `caci.wd1/External` | defense | 200 | 1,533 | 5,311 | yes |
| GDIT | `gdit.wd5/External_Career_Site` | defense | 200 | 1,000 | 7,295 | yes |
| NVIDIA | `nvidia.wd5/NVIDIAExternalCareerSite` | commercial | 200 | 2,000 | 4,946 | n/a |
| Sony | `sonyglobal.wd1/SonyGlobalCareers` | commercial | 200 | 87 | 6,029 | n/a |

**De-risked:**
- The list+detail contract is **identical across all six** (same fields, same shapes).
- **Full JDs** (5-8k chars vs Adzuna's 500). For the defense boards the JD **contains
  the clearance language** — so the deterministic clearance + experience filters would
  finally see the real text. This is the direct root-cause fix for BUG-010.
- **No blocking** on any of the six (no auth, no bot-wall) with a plain User-Agent.
  The pre-spike worry ("most tenants block server reads") did not materialize.

## 5. What the spike did NOT test (the implementation risks for ADR-101)

1. **Volume / rate-limiting.** One list + one detail per board only. At scale
   (pagination + N detail fetches per company per run) Workday may throttle. Mitigation
   the ADR must specify: bound pages, title-filter the listing BEFORE detail fetches
   (do not fetch 1,000-2,000 JDs), bounded concurrency + per-board timeout like the
   dead-link filter (ADR-095).
2. **3-part board id** complicates ADR-098's one-slug verify-on-add UX (tenant + dc +
   site, not a single token).
3. **`postedOn` is a relative string** ("Posted 5 Days Ago"), not ISO — needs a small
   parser, or accept `None` (ADR-080 keeps unknown-age postings).
4. **JD is HTML** -> reuse Greenhouse's `_strip_html`. `locationsText` / `bulletFields`
   normalization shapes still need a closer look for `JobPosting` mapping.
5. **Coverage of `searchText`/facets** — pushing role-filtering server-side could cut
   the per-board volume sharply; worth confirming in the build.

## 6. Recommendation

**GO.** Build a `WorkdayScraper` (implements `scrapers/base.py`, alongside
`GreenhouseScraper`/`LeverScraper` in `app/services/ats_scrapers.py`), scoped to
**Workday only** — iCIMS stays rejected (no clean public JSON API; HTML/anti-bot).
The only genuinely new design concern vs Greenhouse is the two-phase list+detail fetch
and its volume bounding. Proposed as **ADR-101 (pending)**.

## Sources (live, 2026-06-11)

- Booz Allen — https://bah.wd1.myworkdayjobs.com/BAH_Jobs
- Leidos — https://leidos.wd5.myworkdayjobs.com/en-US/External
- CACI — https://caci.wd1.myworkdayjobs.com/en-US/External
- GDIT — https://gdit.wd5.myworkdayjobs.com/External_Career_Site
- NVIDIA — https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
- Sony — https://sonyglobal.wd1.myworkdayjobs.com/SonyGlobalCareers
