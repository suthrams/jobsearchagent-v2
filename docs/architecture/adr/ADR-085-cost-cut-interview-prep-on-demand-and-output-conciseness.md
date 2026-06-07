# ADR-085: Cost cut - interview prep on-demand by default + verbose-agent output conciseness

## Status

Accepted (implemented) (2026-06-06). Driven by a per-profile cost analysis: output
tokens are ~56% of spend, and the in-graph interview coach was auto-firing on nearly
every run.

## Context

A cost analysis of a real profile (27 runs, $7.11) found:

- **Output tokens are ~56% of token cost** (input 44%). Cutting *output* is the
  larger lever; prompt caching is only a ~5-10% lever and is workload-mismatched
  (sporadic runs + concurrent fan-out + sub-1024-token blocks), so it was explicitly
  *not* changed.
- **The in-graph `interview_coach` auto-fired on ~21 of 22 runs** ($1.22, 17% of
  spend, the single most expensive agent). `interview_router` gates on
  `get_min_match_score`, but the selected jobs are *already* the top-N that cleared
  that score - so the top selected job always qualifies and the coach effectively
  always ran.
- The verbose agents (`resume_critic` ~2.7K out, `career_advisor` ~1.7K,
  `resume_reviewer` ~4.1K - at the 4096 cap) drive output cost.

Interview prep is already an out-of-graph on-demand operation (ADR-061:
`POST /workflows/{wf}/jobs/{job}/interview-prep`), so the in-graph auto-fire is
redundant with a cheaper, user-initiated path.

Two non-options were ruled out:
- **Downgrading coach/advisor to Haiku** - previously A/B-validated on Sonnet
  (quality regressed); rejected.
- **Per-agent `max_tokens` caps** - these are structured-output (tool-call JSON)
  agents; a low cap truncates the JSON mid-response and forces schema-repair retries
  (more cost) or errors. `resume_reviewer` already sits at the 4096 cap. Capping is
  the wrong tool; reduce output via the prompt instead.

## Decision

### A. Interview prep is on-demand by default (R4)

New per-profile config knob `scoring.auto_interview_prep` (bool, **default `false`**,
not protected). `interview_router` and the `interview_prep` node now auto-run the
in-graph coach only when `auto_interview_prep` is on **or**
`user_requested_interview_prep` is set. Read via `get_auto_interview_prep(state)`
(never inline). With the default, the coach no longer fires inside the workflow; the
user gets it on demand via the existing endpoint. A profile can set
`scoring.auto_interview_prep: true` to restore the old behavior.

### B. Verbose-agent output conciseness (R5)

Add a conservative brevity instruction to the prompts of `resume_critic`,
`career_advisor`, `interview_coach`, and `resume_reviewer` (one-sentence rationales,
no repetition, no filler). This trims output tokens **without** changing any output
schema - every required field is still produced; only prose verbosity shrinks. Prompt
versions are bumped (ADR-024). This is the safe alternative to `max_tokens` caps. For
`resume_reviewer` it also relieves the 4096-cap truncation pressure.

Both changes are **global** (all profiles) because the levers are global by
architecture: `auto_interview_prep` defaults globally (per-profile override allowed),
and the prompts are shared. They are independent of the per-profile funnel config
(`scoring.min_match_score`, `scoring.max_scored`, `search.relevance_filter`).

## Options considered

- **Remove in-graph interview prep entirely** - rejected; the default-off toggle keeps
  the capability and is reversible per profile.
- **`max_tokens` output caps** - rejected (truncates structured JSON; see Context).
- **Downgrade Sonnet agents to Haiku** - rejected (A/B-validated).
- **Fix prompt caching** - deferred; ~5-10% lever, workload-mismatched, hot-path risk
  (see the cost analysis); not worth the change now.

## Consequences

### Positive

- Removes an always-on Sonnet call from every run (the largest single agent) while
  keeping interview prep one click away (on-demand).
- Trims output tokens on the verbose agents - the dominant (56%) cost component.
- `auto_interview_prep` is per-profile, so power users can opt back in.

### Tradeoffs / limits (honest)

- Interview prep no longer appears automatically; users must request it. The ADR
  trades convenience for cost (the analysis showed it auto-firing nearly always).
- The conciseness edits change output *quality/length*. The structural contract is
  unchanged (schema enforced), but semantic quality should be spot-checked with a
  live integration run; the mock suite only verifies structure.

### Neutral

- No model reassignment; `tests/model_pins.json` unchanged.

## References

- ADR-061 (interview prep as an out-of-graph on-demand op - the path this leans on)
- ADR-024 (prompt versioning - the bumped prompt versions)
- ADR-053/058 (per-agent models - explicitly NOT changed here)
- The profile cost analysis (this session) and the per-profile funnel config it also
  produced (`scoring.min_match_score`, `scoring.max_scored`).
