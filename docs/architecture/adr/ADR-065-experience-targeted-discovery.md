# ADR-065: Experience-Targeted Discovery (Years-of-Experience Cap + Senior Exclusion)

## Status

Accepted (2026-05-26). Implemented.

Extends ADR-064 (per-profile search criteria drive discovery). Same per-profile
config layer (ADR-062) and per-run scraper pattern.

## Context

ADR-064 made a profile's roles drive Adzuna discovery, which lets an entry-level
cybersecurity profile search "Security Analyst" instead of senior architect
titles. But role keywords alone do not express *seniority*: an Adzuna search for
"Security Analyst" still returns "Senior Security Analyst (7+ years)" and similar,
and there is no way to say "I want roles asking for 0-2 years of experience."

The codebase has no experience filter: a job's required experience is not a
structured field (only the free-text `description`), and the legacy
`search.years_of_experience` config (a leftover v1 schema field) was never wired.

This ADR adds a per-profile way to target early-career roles. It is opt-in and
defaults off, so Primary (senior) is unaffected.

## Decision

Three complementary, per-profile levers, all off by default:

### A. Years-of-experience cap (Lever 3 — the primary ask)

New per-profile config `search.max_years_experience: int | None` (default `None` =
off). A deterministic regex (`app/services/experience_filter.py::min_required_years`)
parses each posting's `description` for the lowest stated experience requirement
("5+ years", "3-5 years", "minimum of 2 years", "entry level"/"new grad" -> 0).
`JobDiscoveryService.discover()` drops postings whose parsed minimum **exceeds the
cap**. Postings with **no detectable experience are kept** (mirrors salary's
`ignore_if_missing` — higher recall; a silent JD is not penalized). No LLM cost.

The default cap for a new entry-level profile is **2** (0-2 years).

### B. Senior query exclusion (Lever 1)

New per-profile toggle `search.exclude_senior: bool` (default `False`). When on,
the per-run Adzuna search passes a built-in `SENIOR_TERMS` set as Adzuna's
`what_exclude` query parameter (senior, principal, staff, lead, director, vp,
"head of", manager, architect), so senior postings are dropped at the source.
This requires teaching the v1 `AdzunaScraper` to emit `what_exclude`.

### C. Senior title exclusion (Lever 2)

When `search.exclude_senior` is on, the same `SENIOR_TERMS` are added to the
per-run scraper's title-exclusion list (on top of the default
`EXCLUDED_TITLE_KEYWORDS`), catching senior-titled results that slip past the
fuzzy query exclusion. This is the per-profile `excluded_keywords` extension
deferred in ADR-064, realised as a curated built-in behind one toggle rather than
a hand-curated list.

### Wiring

- `discover_jobs` reads `effective_config.search.max_years_experience` and
  `search.exclude_senior` from state, passes the cap to `discover()`, and passes
  `exclude_senior` into `adzuna_scraper_factory(roles, locations, exclude_senior)`.
- The factory, when `exclude_senior`, builds the per-run Adzuna scraper with
  `what_exclude=SENIOR_TERMS` and `excluded_keywords=EXCLUDED + SENIOR_TERMS`.
- Both knobs are surfaced on the Start New Run form and persisted as the profile's
  defaults when "Save these settings as my defaults" is checked.

### Out of scope

Scoring remains senior-tuned (ADR-064 Decision C). Spelled-out experience ("ten
years") and unusual phrasings are not parsed — the regex covers the common
numeric forms; the cap is a recall-oriented heuristic, not a guarantee.

## Options considered

- **Regex YoE filter on the description (chosen for Lever 3).** No LLM cost,
  deterministic, transparent. Limitation: only as good as what the JD states.
- **LLM-extracted required-experience field per posting.** Rejected for now —
  adds a per-posting LLM call at discovery (cost on every discovered job, the most
  expensive place to add one); the regex covers the common cases for free.
- **Drop postings with no stated experience.** Rejected as the default — many good
  entry roles omit YoE; dropping them hurts recall. Left as a possible future
  policy toggle.
- **Hand-curated per-profile exclusion lists in config.** Rejected as the primary
  UX — a single `exclude_senior` toggle with a curated built-in is simpler; the
  config list remains a possible power-user extension.

## Consequences

### Positive

- An entry-level profile can target 0-x years and exclude senior roles at the
  source and post-fetch, all per-profile and opt-in.
- Deterministic and free (no LLM calls added to discovery).

### Tradeoffs

- The YoE filter depends on JD phrasing; postings that omit or spell out
  experience are kept (by design) and may still be too senior. Pairing the three
  levers mitigates this.
- `SENIOR_TERMS` is a curated built-in; if a profile wants different exclusions it
  needs the (future) explicit `excluded_keywords` override.

### Neutral

- Docs: ADR-065 + index, CLAUDE.md scraper rules, `config_model.md`
  (`search.max_years_experience`, `search.exclude_senior`), `workflow_model.md`,
  `user_guide.md`, `config.example.yaml`. Tests: regex cases, discover()
  cap-filtering (drop > cap, keep None, keep <= cap), factory `exclude_senior`
  wiring.

## References

- ADR-064 — Per-profile search criteria drive discovery (the per-run scraper this
  extends).
- ADR-062 — Multi-user profiles (the per-profile config layer these knobs live in).
- ADR-050 — Wrap the v1 Adzuna scraper (the scraper now also emits `what_exclude`).
