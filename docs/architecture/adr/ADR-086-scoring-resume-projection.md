# ADR-086: Scoring-specific resume projection

## Status

Accepted (implemented) (2026-06-07).

## Context

The Anthropic Messages API is stateless: every scoring call is independent and
must carry the full context (resume profile + the one job). `score_jobs` scores
jobs concurrently, one call per job, so the **resume profile is re-sent on every
per-job scoring call** - up to `max_scored` (8) times per run.

Measured: `scoring_agent` averages ~4,190 input tokens/call; the redacted resume
profile (`trim_resume_profile`) is ~40% of that. Prompt caching is wired (the
`_cached` block) but defeated by the concurrent fan-out (all workers fire before
any writes the cache - ~0 cache reads observed), so the resume is effectively paid
at full input price on each call.

Two non-options were considered and rejected for scoring (see ADR-087 for the
batch alternatives):
- **In-context batching** (resume + all jobs in one call) - loses per-job
  isolation, risks truncating the structured output past `max_tokens`, dilutes
  per-job reasoning. Wrong trade for the most precision-sensitive call.
- **Message Batches API** - 50% off but asynchronous (incompatible with the
  interactive run-and-watch UX). Documented separately as a deferred option.

## Decision

Add `project_resume_for_scoring(profile)` (`app/services/context_trimmer.py`) and
use it in place of `trim_resume_profile(...)` when building the scoring `_cached`
block. It **wraps `trim_resume_profile`** (so the ADR-069 PII seam is preserved -
`raw_text` dropped, identifiers redacted) and then drops fields the Scoring
Agent's prompt provably does not read:

- `name` (already a placeholder), `resume_id`, `file_name` - identity/metadata,
  never part of a fit judgment.
- `skills` - redundant when `skill_groups` is populated (it is the de-duped union,
  ADR-067), so sending both ships the skill list twice. Kept when there are no
  groups (then it is the only source).
- `education[].gpa` / `.honors` (ADR-067) - not part of a fit judgment.

Quality-neutral by the same rule as the other trimmers ("trace the consumer, drop
only unread fields"): the scoring prompt reasons over headline, summary,
experience[] (title/company/years/description/technologies), skill_groups (or
skills), education degree, and certifications - all retained.

`project_resume_for_scoring(` is added to the PII invariant test's
`_REDACTION_HELPERS` allowlist, since it is a sanctioned redaction wrapper.

## Consequences

### Positive
- Shrinks the resume payload re-sent on every per-job scoring call (the redundant
  skills list is the main cut), reducing scoring input tokens with no quality or
  reliability change and no new failure modes.
- Preserves per-job isolation and the per-job cost-cap accounting (unlike batching).

### Tradeoffs / limits (honest)
- Savings are **input-only and modest**: scoring runs on Haiku (cheapest model),
  and output (the dominant 56% of cost) is unaffected. On a large run the cut is a
  few tenths of a cent. This is a hygiene/efficiency change, not a major cost lever
  - the dollars remain in Sonnet output (advisor/critic).
- Only scoring is projected. Research also runs per-job; a research projection is a
  possible follow-up but was out of scope here.

### Neutral
- No schema change, no model change, no API contract change.

## References
- ADR-069 (PII redaction seam - this wraps it)
- ADR-067 (skills as the de-duped union of skill_groups; gpa/honors fields)
- ADR-087 (the async Batches-API scoring alternative, deferred)
