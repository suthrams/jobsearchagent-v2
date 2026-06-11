# BUG-010: Truncated aggregator snippets defeat the clearance + experience filters

- **Severity:** High
- **Status:** Fixed
- **Reported:** 2026-06-11
- **Fixed:** 2026-06-11
- **Area:** `app/services/clearance_filter.py`, `app/services/experience_filter.py` /
  `app/services/seniority_filter.py`, `app/services/job_discovery_service.py`,
  `app/workflows/nodes/discover_jobs.py`
- **Introduced by:** latent since ADR-065 (experience filter) + ADR-094 (clearance
  filter) - both parse the JD body, which Adzuna truncates.

## 1. What happened

A fresh-grad cyber profile (user 1) ran discovery with `exclude_clearance=true`,
`exclude_senior=true`, `max_years_experience=1`. The single job that survived to
scoring was *"SOC Operations/Watch Floor Cybersecurity Analyst - Mid"* (National
gov contractor, via Adzuna). The user reported two defects in that one posting:

1. It **requires a security clearance**, which the profile opted to exclude.
2. It is **mid-level / asks for several years**, which the entry filters should drop.

Both filters were enabled and "working", yet the job sailed through.

## 2. Root cause

**Adzuna stores only a ~500-char truncated snippet** (confirmed: every `adzuna` row
in `jobs` has `LENGTH(job_description) == 500`; greenhouse rows are ~7,800). Both
deterministic filters read the JD **body**:

- `clearance_filter.requires_clearance(desc, title)` - the clearance sentence lives
  in the full JD, past the 500-char cut, so the regex never sees it -> `False`.
- `experience_filter.exceeds_cap(desc, max_years)` - the "X years" requirement is
  likewise past the cut -> not found -> kept.

Secondary gap: the **title** stated both facts ("- Mid", "Watch Floor") and we never
checked it. `exclude_senior`'s term list (`SENIOR_TERMS`) had no mid-level entries
("mid", "II", "III", "tier 2"), and it was only applied inside the Adzuna scraper,
not across sources. Clearance had no title signal for gov-SOC tells.

## 3. Why it was not caught

- Unit tests for both filters fed them **full, clearance/experience-bearing text**
  and asserted a correct drop - so they proved the regex works, never that the
  *input* is reliable. No test fed a realistic **truncated snippet** (the exact
  production condition for the largest source).
- No invariant tied the filters to the **title**, the one field that is never
  truncated; the test suite had no case where the body is silent but the title
  states the level/clearance.
- End-to-end discovery tests run in mock mode with synthetic full descriptions, so
  the 500-char Adzuna reality never appeared.

## 4. Prevention

- **The fix:**
  - `clearance_filter`: added high-precision **title** signals (`watch floor`,
    `watch officer`, `SCIF`, `cleared`) matched against the title regardless of the
    (possibly truncated) body. Stays best-effort on aggregators; documented.
  - New `seniority_filter.title_is_above_entry(title)`: word-boundary detection of
    above-entry titles (senior/staff/lead/**mid**/**II**/**III**/**IV**/**tier 2-3**
    /level 2-3). Applied in `discover_with_stats` across **all** sources when
    `exclude_senior` is set (threaded through from the node); new
    `seniority_title_dropped` funnel stat. Entry titles ("Analyst I", "Tier 1",
    "Junior", "Associate") are kept.
- **Forcing function:**
  `tests/v2/test_bug010_title_filters.py` -
  `test_clearance_caught_by_title_when_body_truncated` (the exact "Watch Floor ...
  Mid" title with a clearance-free 500-char body must be flagged),
  `test_seniority_title_drops_mid_and_levels_keeps_entry`, and
  `test_discover_with_stats_drops_above_entry_titles_when_exclude_senior`.
- **Generalization:** catches the whole class "the field we parse can be truncated,
  so also use the field that cannot (the title)" for both filters - not just the one
  reported posting. Does NOT fully solve clearance on aggregators (a generic title
  with the requirement only in the unseen body still slips).
- **Why ATS-direct is NOT the fix for this user (corrected):** an earlier draft
  named "ATS-direct full text" (ADR-081) as the durable answer. That is only true
  for the *commercial* employers on Greenhouse/Lever - the curated batch (ADR-097,
  33 Greenhouse + 3 Lever) is all consumer-tech / fintech / SaaS, with Palantir the
  lone cleared-adjacent name. The defense / IC contractors that actually post
  cleared SOC / watch-floor roles (Booz Allen, Leidos, SAIC, CACI, GDIT, Peraton,
  ...) run Workday / Taleo / iCIMS, which ADR-081 does not implement - so there is
  no board to fetch even if they were added. For a cleared-cyber profile those
  postings arrive via Adzuna (the truncated source), which makes the title
  heuristics above the PRIMARY defense for this profile, not a stopgap. A real
  durable fix would be a Workday/iCIMS ATS scraper or a bounded full-JD fetch for
  Adzuna survivors when clearance/experience filtering is on (would revisit the
  `_resolve_url` no-op; both are separate ADR-scale follow-ups).
