# ADR-093: Apply-Link Reliability + "Where to Focus" Triage Strip

## Status

Accepted (2026-06-08). UI features (no new agent, schema, endpoint, or limit) from
the jobseeker UX review. Builds on ADR-088 (journey IA + no-tracking guardrail),
ADR-080/081 (posting-age + ATS-direct sources), ADR-071 (active tracks), ADR-089
(Matches as home base).

## Context

The jobseeker UX review found the app is excellent at *judging* a single job but weak
at two recurring needs:

1. **Getting to a working apply link.** Aggregator links (Adzuna) are redirect
   snippets that expire (the ADR-080/081 dead-link arc); employer-direct ATS links
   (Greenhouse/Lever) are the source of truth. The UI showed a bare URL with no signal
   of which kind it was or whether the posting was stale, so a great match often
   dead-ended on a 404.
2. **Knowing where to spend effort.** A search returns 10-25 scored jobs; the user
   opened them one by one. The scorer already produces a best-track score, a one-line
   `match_summary`, and a `recommended_next_action` per job - but that was buried per
   row, so there was no "act on these first" surface.

Both must respect the standing **no application tracking** rule (CLAUDE.md / ADR-088
E): the human owns the decision; the app offers preparation + filtering +
navigation only - never Apply/Save/applied/shortlist/pursuing.

## Decision

### #1 Apply-link reliability (`app/ui/components/posting_link.py`)

Pure helpers + one render function (no LLM, no live verification - Adzuna 429s/JS-gates
that path):
- `source_kind` / `source_badge` classify a job's `source` as **employer-direct**
  (greenhouse/lever), **aggregator** (adzuna/indeed/linkedin), **your link**
  (custom_url), or unknown - and badge it so the user sees link provenance.
- `live_search_url(title, company)` builds a deterministic web-search deep link to find
  the role on the employer's own site.
- `render_posting_links` shows "Open the posting" plus a **"Find the live posting"**
  search fallback whenever the stored link is unreliable (`needs_fallback`: missing,
  aggregator/unknown source, or a stale posting).

Surfaced on the **Opportunity** header (badge + freshness + fallback) and as an
at-a-glance badge/⚠ on the Matches focus cards. UI text says "posting", never "apply".

### #2 "Where to focus" triage strip (`matches.py`)

`_focus_jobs(df, active_tracks, limit=3)` is a pure, deterministic ranking: best
**active-track** score (ADR-071), tie-broken by posting freshness then overall score,
dropping null/zero scores. `_render_focus` shows up to 3 compact cards at the top of
Matches (above the browsable Roles/Companies tabs): title, best-fit progress, link
source + freshness, the one-line why, the suggested next step, and a single **Open ▶**
that jumps into the job's Opportunity page (where tailor / prep / the reliable posting
links live).

It is explicitly framed as **a suggestion of where to spend effort, not a checklist** -
a recommendation, not a status. No new data: it reorders fields the scorer already
produced.

## Consequences

- A dead aggregator link is no longer a dead end (fallback search), and the user sees
  link provenance + staleness before clicking.
- The N-job list becomes a 3-item "act now" plan, leaning on the multi-job system's
  strength - without crossing into tracking.
- Promoting ATS-direct as the *discovery default* (the `scrapers.{greenhouse,lever}.
  companies` config) is intentionally out of scope here - that is an operator config,
  not a UI change. This ADR only surfaces source/staleness and a fallback in the UI.

## PSSR

- **Performance:** deterministic; no new network/LLM. Focus is a sort over the
  already-fetched scored df.
- **Security/Privacy:** the fallback is a public web-search URL built from the job's
  title/company (already on screen) - no PII beyond what is shown.
- **Reliability:** pure functions, unit-tested; the UI degrades gracefully (no
  badge/fallback when source/url is absent).

## Tests

- `tests/v2/test_posting_link.py` - source classification, badge, search-URL encoding,
  `needs_fallback` rules.
- `tests/v2/test_matches_focus.py` - `_focus_jobs` ranking, freshness tiebreak,
  null/zero exclusion, limit, empty input.
- `tests/v2/test_ui_structure.py::test_job_surfaces_have_no_application_tracking` -
  extended to scan `views/matches.py` + `components/posting_link.py` for tracking words.
