# ADR-101: Workday ATS-Direct Scraper

## Status

**Accepted** (2026-06-13; proposed 2026-06-11). Extends the ATS-direct pattern
(ADR-081 Greenhouse/Lever, ADR-097 curated batch, ADR-098 per-profile targeting) to
**Workday**, the missing source-of-truth for the cleared-government employers BUG-010
cares about. The feasibility is de-risked in `spike_workday_ats.md` (6/6 boards, full
JDs, zero blocks).

## Context

BUG-010 root cause: Adzuna stores a ~500-char JD snippet, so the clearance/experience
filters never see the requirement. The cleared-gov employers (Booz Allen, Leidos,
CACI, GDIT, ...) post on **Workday**, not on the Greenhouse/Lever feeds ADR-081
supports. The spike confirmed Workday's undocumented CXS JSON API returns **full JDs**
(5-8k chars) over a stable contract with no auth and no blocking on the probed sample.
So the source-of-truth fix exists; this ADR is how to wire it into the existing
scraper seam without inheriting Adzuna's failure modes or Workday's volume risk.

## Decision

Add a `WorkdayScraper` (implements `scrapers/base.py::BaseScraper`, returns
`list[Job]`). It is **additive, opt-in, per-profile**, and off until a profile lists
Workday boards — identical lifecycle to the other ATS sources (ADR-098).

### Module boundary (modular — own file, not folded into ats_scrapers.py)

Unlike Greenhouse/Lever (one slug, one GET, ~30 lines each), Workday carries genuinely
new, self-contained complexity: a 3-part-id **URL parser**, an **SSRF host guard**, the
**two-phase list+detail fetch**, and a **relative-date parser**. That complexity lives
in a dedicated `app/services/workday_scraper.py` module (`WorkdayScraper`,
`parse_workday_url`, `verify_workday_board`, the date parser, the Workday caps).
`ats_scrapers.py` keeps ONLY the shared seam — `build_ats_scrapers` and
`verify_ats_board` gain a `workday` branch that **lazy-imports** the module (the same
in-function-import pattern already used for `relevance_tokens`, so there is no import
cycle even though the Workday module reuses `_strip_html` / `_title_ok` from
`ats_scrapers`). The factory (`ats_scraper_factory` in `app/api/dependencies.py`) is
unchanged — it already delegates to `build_ats_scrapers`.

**Parsing is single-source.** `parse_workday_url` is the ONLY place that turns a career
URL into `(tenant, dc, site)` and the only place that enforces the host guard. The
verify endpoint returns the parsed triple alongside the job count so the Settings UI
stores exactly what the backend parsed — the UI never re-parses the URL.

### The two-phase fetch (the one genuinely new shape)

Per board, concurrently across boards (like Greenhouse):
1. **List** (paginated, BOUNDED): `POST /wday/cxs/{tenant}/{site}/jobs` with
   `{appliedFacets:{}, limit:20, offset:N, searchText:<primary role or "">}`, pulling
   at most `_WORKDAY_MAX_PAGES` pages (cap the listing at ~100, mirroring
   `_MAX_JOBS_PER_BOARD`).
2. **Title-filter the listing BEFORE any detail fetch** — reuse the existing
   `_title_ok(title, relevant_tokens)` gate (role tokens + `EXCLUDED_TITLE_KEYWORDS`).
   This is the load-bearing volume control: never fetch JDs for the 1,000-2,000 jobs a
   board lists, only for the handful that match the run's roles.
3. **Detail fetch (full JD), CAPPED + bounded concurrency:** for the surviving titles,
   up to `_WORKDAY_MAX_DETAILS_PER_BOARD` (~25), `GET /wday/cxs/{tenant}/{site}{externalPath}`
   -> `jobPostingInfo.jobDescription`, with a per-request timeout and a small worker
   pool (mirroring the dead-link filter, ADR-095). A failed/slow detail logs + skips
   that one job, never the board.

### Field mapping -> `Job`

`title` -> title; `jobDescription` (HTML) -> `_strip_html(...)` -> description;
`locationsText` -> location; company = the board's display label; `source =
JobSource.WORKDAY` (new enum value, both `models/job.py` and
`app/schemas/job_posting.py`); apply `url` = `https://{tenant}.{dc}.myworkdayjobs.com/{site}{externalPath}`
(the employer's own Workday page -> 🟢 employer-direct, no redirect/dead-link issue);
`postedOn` is a RELATIVE string ("Posted 5 Days Ago") -> a small best-effort parser to
a date, else `None` (ADR-080 keeps unknown-age postings).

### Config + verify-on-add (the 3-part board id)

A Workday board is `tenant` + datacenter (`wd1`/`wd5`/...) + `site` — three parts, not
the one slug Greenhouse/Lever use. Decision: the user **pastes the Workday career URL**
(e.g. `https://leidos.wd5.myworkdayjobs.com/External`); a parser extracts
`(tenant, dc, site)`. Stored under `scrapers.workday.companies` as a LIST of those
structured triples (`{tenant, dc, site}`), per-profile (ADR-098 resolution from
`state["effective_config"]`) — NOT the flat slug strings Greenhouse/Lever use.
`verify_ats_board("workday", url)` carries the pasted URL in the existing `slug`
argument (no signature change), lazy-imports `verify_workday_board`, which parses the
URL and probes the list endpoint (returns the open-job count for a healthy board, else
`None`) — the same single source of truth the Settings verify-on-add and
`tools/verify_ats_boards.py` already share. The `POST /config/ats/verify` response for
`workday` ALSO returns the `parsed` triple (`_KNOWN_ATS += "workday"`) so the UI stores
the backend-parsed triple without re-parsing the URL client-side. `build_ats_scrapers`
gains a `workday` branch that builds a `WorkdayScraper` from the stored triples (the
factory in `dependencies.py` is unchanged — it delegates to `build_ats_scrapers`). The
Settings "Target companies" UI gains a Workday add form (paste URL -> verify -> add the
returned triple; existing boards shown as `tenant/site`). ADR-099 `source_label` gains
`workday -> "🟢 Workday"`.

## Boundaries / non-goals

- **Known-list only; NO cross-Workday discovery.** Workday's CXS API is per-tenant —
  each employer is an isolated instance and there is no global "search all Workday
  jobs" endpoint. The scraper queries ONLY the boards a profile has configured (or
  inherits from a curated default batch). Coverage == the configured list. The lever
  to widen coverage is **curation** (a bigger verified board batch, the ADR-097
  pattern), never discovery. This is the inherent ATS-direct trade: full/fresh data +
  employer-direct apply for NAMED companies, vs an aggregator's broad-but-lossy search.
  Workday therefore runs ALONGSIDE Adzuna (breadth), not as a replacement.
- **Workday only.** iCIMS stays rejected (no clean public JSON API; HTML + anti-bot).
- **No default batch yet.** Unlike ADR-097's curated Greenhouse/Lever batch, Workday
  ships **off** (empty default list) until we curate + live-verify a batch in a
  follow-up; a profile opts in by adding boards. (Avoids shipping defense-contractor
  boards to every profile by default — a deliberate, ADR-098-style per-profile choice.)
- **Undocumented API = best-effort.** Never-lose-the-run: any board/detail failure is
  logged + skipped, never raised.

## PSSR

- **Performance / Scalability:** the title-filter-before-detail gate + the per-board
  caps bound the call volume to roughly (pages + matched details) per board; boards run
  concurrently. The risk the spike did NOT measure is Workday rate-limiting under real
  multi-board runs — the caps + bounded concurrency + per-request timeout are the
  mitigation, and a 429/timeout degrades to "skip this board" (never-lose-the-run).
- **Security:** user-supplied board fields are the SSRF surface. The URL parser MUST
  validate the host against the `*.myworkdayjobs.com` pattern and reject anything else,
  in BOTH the scraper and the verify endpoint — never fetch an arbitrary user URL.
- **Reliability:** defensive parsing of an undocumented API; missing/renamed fields
  degrade to skip, not crash.

## Consequences

- **Positive:** the durable, root-cause fix for BUG-010's cleared-gov case — full JD
  text the deterministic clearance/experience filters can finally read, employer-direct
  apply URLs (no dead links), and a large new employer pool (most F500 + defense run
  Workday). Reuses the entire ADR-081/098 seam (factory, verify-on-add, per-profile
  config, source label).
- **Cost:** none new — ATS scrapers are deterministic HTTP, no LLM.
- **Reversibility:** additive, off-by-default, behind a per-profile list; trivially
  removable, same blast radius as ADR-081.
- **Risk:** the undocumented CXS API can change without notice (mitigated by
  never-lose-the-run + the shared `verify_ats_board` health check), and real-volume
  rate-limiting is unproven (mitigated by the caps; revisit if it bites).

## Curated opt-in starter batch (2026-06-13)

Live-verified after the scraper was proven in a real run. **Deliberately NOT shipped as
a config default** — unlike the ADR-097 Greenhouse/Lever batch (broadly-relevant
commercial tech, on by default), the high-value Workday boards are defense contractors
whose jobs are noise for every non-cleared profile. Shipping them on-by-default would
hardcode one profile's use-case into a shared asset, violating the "profile-specifics
live in data, not shared assets / fix the product, not the profile" principle. So the
config default stays **empty**; this batch is a documented, copy-paste **opt-in** set a
profile adds via the Settings Workday add form. Canonical list (career URLs to paste)
lives in `docs/user_guide.md` -> "Target companies -> Workday"; re-verify any one with
`python tools/verify_ats_boards.py <career-url>`.

| Board | Career URL | Type | Open jobs (2026-06-13) |
|---|---|---|---|
| Booz Allen | `https://bah.wd1.myworkdayjobs.com/BAH_Jobs` | defense | 1,976 |
| Leidos | `https://leidos.wd5.myworkdayjobs.com/External` | defense | 2,000 |
| CACI | `https://caci.wd1.myworkdayjobs.com/External` | defense | 1,533 |
| GDIT | `https://gdit.wd5.myworkdayjobs.com/External_Career_Site` | defense | 1,018 |
| NVIDIA | `https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite` | commercial | 2,000 |
| Sony | `https://sonyglobal.wd1.myworkdayjobs.com/SonyGlobalCareers` | commercial | 90 |

## Follow-ups (separate)

- Expand the opt-in batch with more cleared-gov + commercial Workday tenants (SAIC,
  Northrop, RTX, Lockheed, Peraton, ManTech, Parsons, ...), each researched for its real
  career URL then live-verified. Stays opt-in/documented, not a config default.
- Consider a CI re-verification hook for the documented batch (it is NOT in config, so
  `tools/verify_ats_boards.py`'s config path does not cover it today).
