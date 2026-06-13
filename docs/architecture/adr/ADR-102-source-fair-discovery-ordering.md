# ADR-102: Source-Fair Discovery Ordering (Round-Robin Interleave Before the Caps)

## Status

**Proposed** (2026-06-12). Surfaced by the live validation run of profile 1
("Vishal - Cyber grad", run `db64041b`, 2026-06-12) after BUG-012 made
per-profile ATS targeting actually reach discovery. Builds on ADR-081/097/098
(ATS-direct sources) and is orthogonal to the relevance filter (ADR-079) and the
funnel-width knobs (ADR-061).

## Context - the bias the validation run exposed

Discovery concatenates each scraper's results in **scraper-append order**:
`JobDiscoveryService` runs the built-in Adzuna scraper first, then `extend`s the
per-run ATS scrapers (Greenhouse, then Lever). The combined list is then truncated
at **three** order-sensitive points, none of which look at source:

1. `JobDiscoveryService.discover_with_stats` - the service `_max_jobs` cap
   (`postings[: self._max_jobs]`).
2. `discover_jobs` node - `get_max_discovered_jobs(state)` cap
   (`postings[:cap]`).
3. `score_jobs` node - `normalized_jobs[: get_max_scored(state)]`, which decides
   **which jobs get paid research + scoring**.

Because Adzuna is always first, it fills the early slots and the later-appended
ATS sources are truncated first. In the validation run this was stark:

| Source | Raw | In final discovered (13) | **Scored (6)** |
|---|---|---|---|
| Adzuna | 86 | 9 | **6** |
| Greenhouse (Huntress, etc.) | 380 | 4 | **0** |
| Lever (Coalfire, etc.) | 40 | **0** | **0** |

The 4 Huntress SOC-analyst jobs (employer-direct, full JD, exactly the
profile's target) passed the relevance filter but **never got scored** - the
`max_scored=6` slots were all taken by Adzuna jobs that happened to come first
(mostly defense/intel "Digital Network Exploitation Analyst" roles scoring
42-62). Lever fared worse: its jobs were truncated out at the discovery cap, so
0 survived even to the relevance stage. **Per-profile ATS targeting works (ADR-098
+ BUG-012), but its output is then starved by arbitrary list order.** The cost we
pay to query employer-direct boards buys discovery that the caps throw away.

This is not a profile-1 quirk - it is structural. Any profile whose best-fit jobs
come from a non-first scraper loses them to ordering. The reported case is the
forcing test, not the scope (per "fix the product, not the profile").

## Constraints

- **No new user-facing config.** The fix must be automatic; profiles should not
  have to tune a knob to stop losing their ATS jobs.
- **Deterministic.** Same inputs -> same order (no `random`), so runs are
  reproducible and the model-pin / invariant tests stay stable.
- **One seam.** Fix all three truncation points with a single change, not three.
- **Additive / source-agnostic to the rest of the funnel.** Dedup, filters,
  relevance, and scoring logic are unchanged; only the *order* they see changes.

## Decision

Interleave the surviving postings by source **once, immediately after dedup and
before the first `_max_jobs` cap** in `discover_with_stats`, using a deterministic
**round-robin** over sources:

```
group postings by source, preserving within-source order
emit one from each non-empty group in round-robin until all groups are drained
  -> [adzuna_0, greenhouse_0, lever_0, adzuna_1, greenhouse_1, lever_1, ...]
```

Round-robin gives every source proportional representation through each cap with
**no source hierarchy** - it does not claim employer-direct is "better," only
that no source should be starved by append order. Source grouping uses the
existing `Job.source` field (the same one ADR-099 surfaces in the UI). Within a
source, the prior order is preserved, so any upstream ranking a scraper applies
survives.

Placement is the load-bearing choice: doing it post-dedup / pre-cap means the
**single** reorder makes all three downstream truncations source-fair at once. A
new `discovery_stats["source_mix"]` records the per-source counts that survive to
`returned`, so the bias (or its absence) is observable on the Workflow Detail
screen instead of inferred.

## Decision review (so this is not a rubber-stamp)

- **Recommendation:** round-robin interleave, post-dedup, no new config.
  **Confidence: medium-high.** The mechanism and placement are well understood
  (the validation run is direct evidence); the residual uncertainty is whether
  fair-share is the right *product* stance vs. employer-direct-first (see below).
- **The ONE load-bearing decision:** *where* to interleave. Post-dedup/pre-cap
  fixes all three caps with one change; interleaving only in `score_jobs` would
  leave the two discovery caps still Adzuna-biased (Lever would keep getting
  truncated before it is ever seen). Everything else is mechanical.
- **Alternatives considered:**
  - *Employer-direct first* (sort ATS ahead of Adzuna). Encodes the ADR-081/099
    thesis that employer-direct is higher-signal. **Rejected as the default**
    because it can starve Adzuna's coverage when ATS volume is high (Greenhouse
    returned 380 raw here) and bakes a value judgment into ordering. De-bundled
    as a possible follow-on weighting (below).
  - *Per-source quota* (reserve N slots for ATS). More control but needs a new
    config knob - violates the "no new config" constraint. De-bundled.
  - *Quality-ranked ordering* (order by predicted fit). Circular: scoring is the
    thing we are selecting *for*; we cannot rank by score before scoring.
- **Pros:** kills the starvation at its root for every profile/source; one seam;
  no config; deterministic; makes the mix observable.
- **Cons / where I took the easy path:** round-robin treats a 380-raw source and
  a 40-raw source as equal *per round*, so a high-volume source still contributes
  more in absolute terms only after the small sources drain - which is the
  intended fairness, but it means a source with very few postings gets
  over-represented per-round relative to its raw share. For the cap sizes we use
  (<=50 discovered, <=25 scored) this is a feature, not a bug. I did **not** add
  weighting; if it matters later, that is the de-bundled follow-on.
- **Risks (estimated, not measured):** (1) reordering changes which jobs appear
  first in the discovered/Matches lists and in manual-selection - intended, and
  consistent with ADR-099 source visibility, but it *is* a visible behavior
  change. (2) A handful of tests assert discovery order; they will need updating
  to the interleaved order (that is the forcing function working, not a
  regression).
- **Reversibility:** high. The interleave is a single pure function call at one
  seam; removing it restores append order. No schema or config change to roll
  back. `source_mix` is additive stats.
- **Reasons to say NO / defer:** if the funnel caps were generous enough that
  every source survives anyway, this would be premature - but the validation run
  shows they are not (Lever -> 0). If we would rather *prioritize* employer-direct
  outright, pick that ADR instead; this one deliberately does not.

## How it integrates with the workflow

Unchanged everywhere except order. `discover_with_stats`: normalize -> filters
(title/experience/seniority/age) -> dedup -> **interleave_by_source (new)** ->
dead-link -> `_max_jobs` cap -> stats. Downstream (`discover_jobs` node cap,
relevance filter ADR-079, `score_jobs` cap, auto-select, deep review) is
untouched; it simply sees a source-fair order. Senior tuning stays
`scoring.min_match_score`; funnel width stays the ADR-061 knobs.

## Out of scope - explicitly de-bundled

- **Employer-direct weighting / priority.** Making ATS rank ahead of aggregators
  (not just fair-share). A separate decision with its own product trade-off.
- **Per-source quota config knob.** `scoring.min_ats_scored` or similar.
- **Quality/fit pre-ranking before the scoring cap.** Would need a cheap
  pre-score signal; large, separate.

## PSSR

- **Performance:** one O(n) group-and-interleave over <=~500 postings per run;
  negligible vs. the network + LLM cost already incurred.
- **Scalability:** no new storage; `source_mix` is a small dict in
  `discovery_stats` (already persisted).
- **Security:** none - operates on already-fetched, already-redacted-downstream
  postings; no new external input, no PII surface.
- **Reliability:** pure deterministic function; empty/single-source inputs are
  identity. Never-lose-the-run is preserved (interleave reorders, never drops).

## Tests (at implementation)

- Unit: `interleave_by_source` - round-robins three sources, preserves
  within-source order, identity on single-source and empty inputs, stable
  (deterministic) across calls.
- Funnel: a `discover_with_stats` test with an Adzuna-heavy + small-ATS fixture
  asserts ATS postings survive a tight `_max_jobs` cap (the regression guard for
  the validation-run starvation) and `source_mix` reflects the kept counts.
- Update the existing order-asserting discovery tests to the interleaved order.

## References

- Live validation run `db64041b` (profile 1), 2026-06-12 - the forcing evidence.
- ADR-081 / ADR-097 / ADR-098 (ATS-direct sources, curated batch, per-profile).
- ADR-099 (source visibility) - same `Job.source` field.
- ADR-061 (funnel-width caps) - the truncation points this reorders ahead of.
- `bugs/BUG-012` - the fix that made ATS targeting reach discovery, exposing this.
