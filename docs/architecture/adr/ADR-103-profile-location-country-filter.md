# ADR-103: Profile-Derived Location Filter (Drop Out-of-Country Postings at Discovery)

## Status

- **Proposed** (2026-06-12). Surfaced by live validation run `db64041b` (profile 1).
- Companion to ADR-102 (ordering); same discovery-filter family as ADR-079/080/095.

## Context

- Adzuna filters country at query time (`scrapers.adzuna.country=us`).
- ATS-direct scrapers (ADR-081 Greenhouse/Lever) have **no** location gate - they return a company's **entire global board**.
- Result: a US-only profile got Ireland + UK roles (Huntress board). Out-of-country leak is **ATS-only** and **structural** (any profile pulling ATS boards).

## Constraints

- **Profile-derived**, not hardcoded US-only (correct for UK/Canada/multi-country profiles).
- **Uniform across sources** (defense in depth, covers any future source with the gap).
- **Never lose the run**: drop only a *confidently-resolved* foreign country; keep anything ambiguous.
- Deterministic, no LLM, no network, **no new heavy dependency**.

## Decision

- Add `app/services/location_filter.py`:
  - `country_of(location) -> str | None` - US states/codes/aliases -> `"US"`; curated lexicon -> ISO-2; `None` = can't resolve (keep). Short aliases ("us","uk") match whole tokens only (so "Houston" != US).
  - `derive_allowed_countries(locations) -> set[str]` - the profile's set from its own `search.locations`; empty = no-op.
  - `filter_by_location(postings, allowed) -> (kept, dropped, samples)` - drop iff country non-None and not in `allowed`.
- Wire into `discover_with_stats` (after age filter, before dedup), gated by `search.restrict_to_profile_locations`.
- `discovery_stats` gains `location_dropped` + `location_samples` (PII-safe: title/company/location/url).
- **Default ON**: the filter only drops a *confidently* out-of-country posting, so default-on is safe and matches user intent; toggle exists for reversibility / deliberately-global profiles.

## Decision review (not a rubber-stamp)

- **Recommendation:** profile-derived, uniform, default-on, keep-on-ambiguity. **Confidence: high** (mechanism), **medium** (default-on is a behavior change, mitigated by safeguards).
- **Load-bearing decision:** the drop *confidence threshold* - drop only on a confidently-resolved foreign country, never on ambiguity. This is what makes default-on safe (can't drop a US job with a sparse string). Cost: recall (an unresolved foreign posting survives - acceptable).
- **Alternatives:**
  - Explicit `search.allowed_countries` knob - more control, more to keep in sync; de-bundled (layer later).
  - ATS-only filter - narrower; same code covers all sources uniformly, so rejected.
  - Geocoding lib (pycountry/geonames) - heavier dep for marginal gain; rejected, lexicon is extensible.
- **Pros:** closes the leak for every profile; profile-relative; cheap/deterministic; observable via `location_dropped`.
- **Cons / easy paths taken:** lexicon not exhaustive (obscure country -> `None` -> survives); no city/region -> country resolution. Both are recall gaps, not correctness risks.
- **Risks (estimated):** a US string mis-read as foreign would wrongly drop (mitigated: aggressive US matching, foreign names as whole-token/full matches, tests on the exact run strings); default-on shifts counts for profiles with detectable scope.
- **Reversibility:** high - one filter, one seam, behind `restrict_to_profile_locations`. No schema change.
- **Reasons to say NO:** a profile wanting global/remote-anywhere roles (use the toggle); or preferring explicit scope (use the knob instead).

## How it integrates

- `discover_with_stats`: normalize -> title/experience/seniority/age -> **location (new)** -> dedup -> dead-link -> interleave (ADR-102) -> `_max_jobs` cap -> stats.
- Additive to Adzuna's query-time country filter; primarily catches the ATS leak. Nothing downstream changes.

## Out of scope (de-bundled)

- `search.allowed_countries` knob (allow countries beyond the derived set).
- City/region -> country geo-resolution (needs a dataset).
- Within-country distance/radius filtering.

## PSSR

- **Performance:** O(n) string resolution over <=~500 postings; no network.
- **Scalability:** no storage; `location_samples` capped.
- **Security:** already-fetched postings; samples PII-safe (same classes as dead-link samples).
- **Reliability:** pure deterministic; empty allowed / unparseable -> keep-all.

## Tests

- `country_of`: US forms, foreign forms, ambiguous -> None, no-substring guard ("Houston").
- `derive_allowed_countries`: profile-1 -> {"US"}; all-Remote -> empty.
- `filter_by_location`: drops Ireland/UK vs {"US"}, keeps US + ambiguous; empty allowed -> keep all.
- Funnel: US + Ireland/UK fixture, US profile -> foreign dropped, `location_dropped` recorded.
- File: `tests/v2/test_adr103_location_filter.py`.

## References

- Validation run `db64041b` (foreign-jobs evidence); ADR-081 (unfiltered ATS boards); ADR-065/080/095 (keep-on-ambiguity pattern); ADR-102 (sibling fix).
