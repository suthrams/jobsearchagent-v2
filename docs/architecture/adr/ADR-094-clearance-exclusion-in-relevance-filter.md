# ADR-094: Security-Clearance Exclusion (Folded into the Relevance Filter)

## Status

Accepted (2026-06-08). Extends ADR-079 (relevance pre-filter). Deterministic filter +
one opt-in config knob; no new agent or endpoint.

## Context

Many postings (esp. US defense/gov) require an active security clearance (Secret,
Top Secret, TS/SCI, polygraph, DoD, ...). A candidate who can't or won't pursue
cleared work wants those dropped before paying to score them. The owner asked that
this exclusion **live with the relevance filter** ("part of the relevance filter, if
selected") rather than as a wholly separate stage.

Two design facts shaped the decision:
- Clearance requirements are stated in plain text, so detection is **fully
  deterministic** (keyword/phrase) - it needs no LLM.
- Some profiles *do* hold a clearance and *want* cleared roles, so the exclusion must
  be **opt-in**, not automatic for everyone who uses the relevance filter.

## Decision

### Deterministic detection (`app/services/clearance_filter.py`)

`requires_clearance(description, title)` matches a precision-tuned set of qualified
phrases ("security clearance", "secret clearance", "top secret", "TS/SCI",
"polygraph", "<gov/active/obtain> ... clearance", "clearance required/eligible", ...).
It deliberately does **not** trip on bare "clearance" / "secret" / the "Security+"
cert, so "clearance sale" and "secret sauce" are safe. No LLM, no network.

### Folded into the relevance_filter node, opt-in

A new `search.exclude_clearance` flag (default **off**). When the relevance_filter
node runs (i.e. `search.relevance_filter` is on) **and** `exclude_clearance` is on, the
node partitions clearance-gated postings out **deterministically, before the LLM
call** - so they cost zero tokens and are dropped reliably regardless of the agent's
verdict. They are recorded in `discovery_stats.relevance_drops` with
`mismatch="requires_clearance"` and counted in `discovery_stats.clearance_dropped`.
The remaining candidates go to the LLM relevance pass as before.

The UI surfaces it as a sub-checkbox indented under the relevance filter in New search
("↳ also exclude jobs requiring a security clearance"), with help noting it has no
effect unless the relevance filter is on.

### Why opt-in + under the relevance filter (not automatic, not standalone)

- **Opt-in** protects cleared candidates: enabling the relevance filter for seniority
  matching must not silently delete every cleared role.
- **Under the relevance filter** matches the owner's mental model and reuses the
  "drop jobs that don't fit before scoring" stage. (A free-standing toggle that worked
  without the relevance filter was considered and declined in favour of this grouping.)

## Consequences

- Clearance exclusion is free (deterministic) and reliable (drops even if the LLM pass
  fails - the node keeps the non-clearance candidates on agent error, clearance stays
  dropped).
- It only takes effect when the relevance filter is enabled - a deliberate coupling.
  If a profile wants clearance exclusion without the LLM relevance pass, that would be
  a future standalone toggle (out of scope here).
- `discovery_stats` gains `clearance_dropped`; the existing `relevance_drops` audit now
  includes `requires_clearance` rows.

## PSSR

- **Performance/Cost:** deterministic; partitioned out *before* the LLM call, so it
  also trims the relevance-pass token count.
- **Security/Privacy:** operates on the already-discovered job description/title; no PII
  beyond the existing relevance-filter seam (profile still enters only via
  `trim_resume_profile`).
- **Reliability:** never-lose-the-run preserved - a detection fault keeps the job (it
  falls through to the LLM); an LLM fault keeps the non-clearance candidates.

## Tests

- `tests/v2/test_clearance_filter.py` - predicate precision/recall (positives,
  false-positive guards, title signal, None-safe).
- `tests/v2/test_adr079_relevance_filter.py` - node drops clearance before the LLM &
  not sent to the agent (flag on), keeps them (flag off), and skips the LLM call when
  clearance removes every candidate.
