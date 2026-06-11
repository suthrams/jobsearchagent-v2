# ADR-101: Workday ATS-Direct Scraper

## Status

**Proposed** (2026-06-11). Extends the ATS-direct pattern (ADR-081 Greenhouse/Lever,
ADR-097 curated batch, ADR-098 per-profile targeting) to **Workday**, the missing
source-of-truth for the cleared-government employers BUG-010 cares about. The
feasibility is de-risked in `spike_workday_ats.md` (6/6 boards, full JDs, zero blocks).

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
`list[Job]`) in `app/services/ats_scrapers.py`, alongside `GreenhouseScraper` /
`LeverScraper`. It is **additive, opt-in, per-profile**, and off until a profile lists
Workday boards — identical lifecycle to the other ATS sources (ADR-098).

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
`(tenant, dc, site)`. Stored under `scrapers.workday.companies` as that structured
triple (per-profile, ADR-098 resolution from `state["effective_config"]`).
`verify_ats_board` gains a `workday` branch that parses the URL and probes the list
endpoint (returns the open-job count for a healthy board, else `None`) — the same
single source of truth the Settings verify-on-add and `tools/verify_ats_boards.py`
already share. `build_ats_scrapers` / `ats_scraper_factory` gain a `workday` branch.
The Settings "Target companies" UI gains a Workday add form (paste URL -> verify ->
add). ADR-099 `source_label` gains `workday -> "🟢 Workday"`.

## Boundaries / non-goals

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

## Follow-ups (separate)

- Curate + live-verify a default Workday board batch (the ADR-097 equivalent), once the
  scraper is proven in a real run.
- The 3-part-id verify-on-add UX may want a dedicated test (parser + host-validation).
