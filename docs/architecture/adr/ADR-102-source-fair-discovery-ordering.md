# ADR-102: Source-Fair Discovery Ordering (Round-Robin Interleave Before the Caps)

## Status

- **Accepted (implemented)** (2026-06-12). Surfaced by live validation run `db64041b` (profile 1) after BUG-012 made per-profile ATS targeting reach discovery.
- Builds on ADR-081/097/098 (ATS sources); orthogonal to ADR-079 (relevance) and ADR-061 (funnel width).

## Context

- Discovery concatenates scraper results in **append order** (Adzuna built-in first, then ATS Greenhouse, then Lever).
- Three order-sensitive caps truncate a *prefix*, none source-aware: service `_max_jobs` cap, `discover_jobs` node cap, `score_jobs` scoring cap.
- Adzuna-first => later sources starved. Validation run:

| Source | Raw | In final 13 | **Scored (6)** |
|---|---|---|---|
| Adzuna | 86 | 9 | **6** |
| Greenhouse (Huntress) | 380 | 4 | **0** |
| Lever | 40 | **0** | **0** |

- The 4 Huntress SOC roles (employer-direct, the profile's target) passed relevance but were never scored; Lever was truncated out before relevance. **Structural, not profile-specific.**

## Decision

- Interleave surviving postings **round-robin by source**, once, **post-dedup / pre-cap**, in `discover_with_stats`:
  - `[adzuna_0, greenhouse_0, lever_0, adzuna_1, greenhouse_1, ...]`
- Properties: no source hierarchy; deterministic (no randomness); within-source order preserved; **reorders only, never drops**.
- One reorder makes all three caps source-fair (the load-bearing placement choice).
- `discovery_stats` gains `source_mix` (per-source survivor counts) for observability.

## Decision review (not a rubber-stamp)

- **Recommendation:** round-robin, post-dedup, no new config. **Confidence: medium-high** (mechanism/placement clear from the run; residual question is fair-share vs employer-direct-first as the product stance).
- **Load-bearing decision:** *where* to interleave. Post-dedup/pre-cap fixes all three caps with one change; interleaving only in `score_jobs` would leave the two discovery caps Adzuna-biased (Lever keeps getting truncated unseen).
- **Alternatives:**
  - Employer-direct first (sort ATS ahead of Adzuna) - encodes "employer-direct is better" but can starve Adzuna coverage; **rejected as default**, de-bundled.
  - Per-source quota (reserve N slots) - needs a new config knob; de-bundled.
  - Quality-ranked ordering - circular (scoring is what we're selecting *for*).
- **Pros:** kills starvation at the root for every profile/source; one seam; no config; deterministic; observable.
- **Cons / easy path taken:** round-robin treats a 380-raw and a 40-raw source equally per round (intended fairness at our cap sizes <=50/<=25); no weighting added (de-bundled follow-on).
- **Risks (estimated):** changes discovered/Matches list order (intended, consistent with ADR-099); a few order-asserting tests need updating (the forcing function working).
- **Reversibility:** high - a single pure-function call at one seam; remove to restore append order. No schema change.
- **Reasons to say NO:** if caps were generous enough that every source survives (they're not - Lever -> 0); or if we'd rather *prioritize* employer-direct (pick that ADR instead).

## How it integrates

- `discover_with_stats`: normalize -> filters -> dedup -> location (ADR-103) -> dead-link -> **interleave (new)** -> `_max_jobs` cap -> stats.
- Downstream (node cap, relevance, scoring cap, auto-select, deep review) unchanged - it just sees a source-fair order.

## Out of scope (de-bundled)

- Employer-direct weighting/priority (rank ATS ahead, not just fair-share).
- Per-source quota knob (`scoring.min_ats_scored`).
- Quality/fit pre-ranking before the scoring cap.

## PSSR

- **Performance:** one O(n) group-and-interleave over <=~500 postings; negligible.
- **Scalability:** no storage; `source_mix` is a small dict in `discovery_stats`.
- **Security:** operates on already-fetched postings; no new input/PII surface.
- **Reliability:** pure deterministic; empty/single-source = identity; never drops.

## Tests

- Unit: round-robin 3 sources, within-source order, identity (empty/single), determinism, never-drop, `source_mix`.
- Funnel: Adzuna-heavy + small ATS fixture, tight cap -> ATS survives (regression guard); `source_mix` reflects kept counts.
- File: `tests/v2/test_adr102_source_interleave.py`.

## References

- Validation run `db64041b` (forcing evidence); ADR-081/097/098 (ATS sources); ADR-099 (source field); ADR-061 (the caps); `bugs/BUG-012` (the fix that exposed this).
