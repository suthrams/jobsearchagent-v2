# ADR-099: Job Source Visibility in the Discovered + Matches Lists

## Status

**Accepted** (2026-06-11) — implemented. Picks up the "Source visibility in lists"
item explicitly de-bundled from ADR-098.

## Context

Each job's origin (`greenhouse`, `lever`, `adzuna`, `indeed`, `linkedin`, `manual`)
has been **persisted** in `jobs.source` since discovery (ADR-081), and the apply-link
component classifies it into a reliability badge — 🟢 Employer-direct / 🟡 Aggregator
link — shown on the Matches *focus cards* and the Opportunity detail (ADR-093).

What was missing: the **tabular lists** (the Matches "Roles" table and the Workflow
Detail "all discovered jobs" table) had **no source at all**, and even the badge only
told you the *class* (employer-direct), never *which* source — you could not tell a
Greenhouse hit from a Lever or Adzuna one without opening the DB. With per-profile ATS
targeting now live (ADR-098), "which of my boards actually produced this match" is a
question users ask, and the data was already there — just not surfaced.

## Decision

Surface the **exact source name** (with the existing reliability-colour icon) wherever
a job is listed. One small, deterministic, pure helper drives every surface.

- **`formatting.source_label(source)`** — a pure function (lives in the already-pure
  `app/ui/formatting.py`, no Streamlit import) mapping a stored source string to a
  display label: `greenhouse -> "🟢 Greenhouse"`, `lever -> "🟢 Lever"`,
  `workday -> "🟢 Workday"` (ADR-101, employer-direct),
  `adzuna -> "🟡 Adzuna"`, `indeed -> "🟡 Indeed"`, `linkedin -> "🟡 LinkedIn"`,
  `manual -> "🔗 Custom URL"`, empty/unknown -> `""` (or a neutral `•` + titlecased
  raw). The 🟢/🟡 carry the same employer-direct-vs-aggregator reliability meaning as
  the ADR-093 badge, so nothing about link-reliability semantics changes.
- **Matches "Roles" table** — a new **Source** column (`TextColumn`), so the source is
  sortable-by-eye alongside Company/Score and visible without selecting a row.
- **Workflow Detail "all discovered jobs" table** — `build_discovered_rows` adds a
  `Source` field, so even *unscored* discovered jobs show where they came from (useful
  for "my Greenhouse boards returned these, Adzuna returned those").
- **Matches focus cards + Opportunity detail** — upgraded from the class-only
  `source_badge` ("Employer-direct") to the exact `source_label` ("🟢 Greenhouse").
  More informative, same colour.

The reliability-class helper `posting_link.source_badge` / `source_kind` stays
(unchanged) — `source_kind` still drives the apply-link fallback (`needs_fallback`,
ADR-093); this ADR does not touch that path.

## What this is NOT

- **Not a new column in the DB or API** — `source` was already persisted and already
  travels in the scored-jobs / discovered-jobs payloads. Pure presentation.
- **Not a filter** — surfacing only. A "filter by source" control is a possible later
  addition, not this decision.
- **No application tracking** — this shows provenance of a posting, never an
  Apply/Save/status (CLAUDE.md guardrail).

## Decision review

- **Recommendation:** the pure-`source_label` approach. **Confidence: high** — it is a
  display-only change over data that already exists, fully reversible (drop the column
  / revert the helper).
- **The one choice that matters:** show the **exact source name** vs keep the
  **reliability class only**. Exact name is strictly more informative for the
  ADR-098 question ("which board produced this") and keeps the colour, so it dominates.
- **Cons / traded away:** a lowercase enum like `indeed` (Adzuna aggregates Indeed)
  can read oddly next to `adzuna`; the label map titlecases knowns and leaves unknowns
  as a neutral dot + raw string rather than inventing names.
- **Reasons to say no / differently:** if you wanted source as a *filter* (not just a
  column) this is only the floor; if you wanted to keep the table minimal, the badge on
  the card already hinted at class. We judged the explicit column worth the width.

## PSSR

- **Performance:** one pure dict lookup per row; no I/O, no network, no LLM.
- **Scalability:** unchanged — same rows, one extra string column.
- **Security:** source is a coarse provenance label (a feed name), never PII; no new
  data leaves the system.
- **Reliability:** pure + total (unknown/empty -> safe empty/neutral label); cannot
  raise.

## Tests

`source_label` mapping (each known source, empty, None, unknown passthrough);
`build_discovered_rows` includes a correct `Source`; UI smoke (Matches + Workflow
Detail render with the new column). Docs sweep: ADR-099 + index, ADR-098 out-of-scope
note (this item now shipped), `features.md`, `user_guide.md`, `wiki.md`, CHANGELOG.

## References

- ADR-098 — per-profile ATS targeting (de-bundled this item).
- ADR-093 — apply-link reliability badge + `source_kind` (the reliability class kept here).
- ADR-081 — ATS-direct sources (where `greenhouse`/`lever` originate).
