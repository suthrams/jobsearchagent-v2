# ADR-079: Reasoning Relevance Pre-Filter Between Discovery and Scoring

## Status

Accepted (2026-06-04). Implemented.

Extends ADR-064 (per-profile search criteria drive discovery) and ADR-065
(experience-targeted discovery). Same per-profile config layer (ADR-062). Reuses
the wide-net-then-narrow shape of ADR-060/061 (manual scoring selection), but the
triage is performed by a cheap LLM instead of a human.

## Context

A fresh-graduate profile still receives senior-level and sometimes unrelated jobs
all the way into scoring. The two filters between discovery and scoring are both
deterministic and conservative by design, which is precisely why they leak:

- **Senior exclusion (ADR-064/065 Levers 1-2)** is keyword-based (`SENIOR_TERMS`):
  it catches "Senior X" but misses "Lead", "Staff", "Principal", or a role that is
  senior in substance without a senior word in the title.
- **Experience window (ADR-065 Lever 3)** parses the JD with regex for stated
  years. By design a posting with **no detectable years is kept** (recall over
  precision). Many senior JDs never state a number, so they pass.
- **Title relevance gate** is a shallow substring match on role tokens; tangential
  keyword hits ("unrelated roles") survive it.

Nothing today asks the semantic question the user actually wants answered: *is
this role appropriate for THIS candidate's level and target, reasoning over the
full posting?* That requires a reasoning pass, not another regex.

ADR-065 explicitly rejected an LLM experience-extractor because it would add a
per-posting LLM call **at discovery** — "cost on every discovered job, the most
expensive place to add one," paid on every run. This ADR is not that. It adds
**one batched, cheap (Haiku) call per run**, **opt-in per profile**, placed
**after** discovery, that **drops** mismatched jobs before scoring — so it removes
2 LLM calls (research + scoring) for every job it sheds. For a noisy early-career
profile it is net cost-negative.

## Decision

Add an opt-in, per-profile **relevance pre-filter**: a new cheap reasoning agent
that runs once per run between `load_resume` and `score_jobs` on the auto-scoring
branch, hard-dropping jobs that are a seniority or relevance mismatch for the
profile before any scoring spend is paid.

### A. New config knob (opt-in, per profile)

`effective_config.search.relevance_filter: bool` (default `False`). Off leaves
Primary and every existing run byte-for-byte unchanged. On for the fresh-grad
profile. Not in `_PROTECTED_KEYS` (the user owns it); `ConfigService._enforce_limits`
coerces to bool. Surfaced on the Start New Run form and persisted as a profile
default like `exclude_senior`.

### B. Wide net, then narrow (the ADR-060 shape, automated)

In auto mode, `get_max_discovered_jobs` returns the **scored** cap — there is no
point discovering more than we will score. That assumption no longer holds once a
filter sits in the middle: if discovery caps to 10 first, noise fills the 10 slots
before the filter sees the good jobs. So when `relevance_filter` is on (and manual
selection is off), discovery casts the **wide net** (`MAX_DISCOVERED_JOBS`, same as
manual mode), the filter triages it, and `score_jobs` narrows the survivors to
`get_max_scored` as it already does. This is exactly ADR-060's curate-before-scoring
flow with a cheap LLM standing in for the human, and no `interrupt()` (ADR-059
stands).

### C. New `RelevanceFilterAgent` (cheap, batched, structured output)

- `AGENT_NAME = "relevance_filter"`, prompt `app/prompts/agents/relevance_filter.txt`,
  Haiku by default via `ModelRegistry` (the cheapest tier, like the Scoring Agent).
- **One** call per run. Context: the **redacted** profile (target roles, seniority
  signal, years window from `effective_config.search`) plus the discovered jobs as
  `{job_id, title, company, truncated_description}`. Descriptions are truncated to
  bound tokens; the set is bounded by `MAX_DISCOVERED_JOBS`.
- Output schema `RelevanceFilterResult { verdicts: list[RelevanceVerdict] }`, each
  `RelevanceVerdict { job_id: str, keep: bool, mismatch: Literal["none","too_senior","too_junior","unrelated"], reason: str }`
  (`reason` PII-safe — about the posting, never the candidate). Validated by Pydantic
  before use, like every agent output.
- Judges fit **relative to the profile's own target level**, in both directions:
  the **seniority** axis drops roles outside the candidate's band — `too_senior`
  for an early-career profile, `too_junior` for a senior profile — and the
  **relevance** axis drops roles unrelated to the profile's target roles/domain
  (`unrelated`). The candidate's band is inferred from the profile (years, titles)
  and the explicit `search` signals (`min_years_experience` / `max_years_experience`,
  `exclude_senior`). This makes the single toggle the LLM counterpart to ADR-065's
  symmetric deterministic pair (`exceeds_cap` for the max bound, `below_floor` for
  the min bound) — one feature that serves a fresh-grad profile and a senior profile
  alike. Conservative: drop only on a clear mismatch; when unsure, keep
  (recall-biased, mirrors ADR-065).

### D. New `relevance_filter` node + routing

- Node `relevance_filter` (instrumented by `_instrument_step` like every node).
  Reads `normalized_jobs` + redacted `resume_profile` from state, runs the agent,
  and returns `normalized_jobs` narrowed to the kept set (discovery order
  preserved, so the existing title-relevance ordering still feeds the scored cap).
  Dropped jobs are recorded in `discovery_stats` (count + per-job reason) and stay
  in the `jobs` table — only the run's working set is narrowed.
- The call increments the run's `llm_calls` metric (1) and counts against
  `MAX_LLM_CALLS_PER_RUN` via `add_llm_calls_bulk`, like `score_jobs`.
- `scoring_mode_gate` (the `load_resume` edge) gains a third target: manual
  selection -> `await_scoring_selection`; else relevance_filter on -> `relevance_filter`;
  else -> `score_jobs`. New edge `relevance_filter -> score_jobs`.
- Phase-2 manual scoring re-entry (`entry_router`, `phase="scoring"`) is untouched:
  it enters at `score_jobs` directly, and in manual mode the human already triaged,
  so the filter never runs there.

### E. Never-crash, never silently drop everything

If the filter call fails or returns an unparseable/empty result, the node logs the
error to `errors[]` and **keeps all** discovered jobs (falls through to scoring the
unfiltered, capped set). A filter fault must never cost the user their entire run.
The agent's own observability (`agent_events`/`llm_calls`) is logged by `BaseAgent`;
a `relevance_filter` security event is **not** added (no guardrail is being
enforced against untrusted input here — the JD-as-data rule below is the existing
guardrail).

### Security

- Job descriptions are untrusted input. The prompt includes
  `prompts/shared/guardrails.txt` and treats every posting as data, never
  instructions (existing invariant).
- The profile enters the agent context only through `trim_resume_profile()` /
  `redact_pii_for_llm()` (ADR-069), exactly as `score_jobs` does, so
  `test_pii_redaction_invariant.py` stays green.

## Options considered

- **Strengthen the deterministic filters only** (more `SENIOR_TERMS`, smarter
  regex). Rejected as the primary fix: the failures are semantic ("senior in
  substance", "tangentially related"), which keyword/regex cannot express. The
  deterministic filters remain as a free first pass; this adds the reasoning layer
  on top.
- **Per-posting LLM extraction at discovery** (the ADR-065 rejection). Rejected
  again for the same reason: cost at the most expensive place, on every run.
- **One batched cheap call after discovery, opt-in (chosen).** Bounded cost (1
  call/run), net cost-negative when it drops jobs, and only runs for profiles that
  ask for it.
- **Keep-and-flag instead of hard-drop.** Rejected for this profile's need: it
  saves no scoring spend (the user pays to score the noise anyway). Hard-drop with
  a logged reason gives the cost win and remains auditable via `discovery_stats`.
- **Let the Scoring Agent hard-reject.** Rejected: scoring already costs the 2
  calls we are trying to avoid; the point is to filter *before* paying them.

## Consequences

### Positive

- A fresh-grad (or any early-career) profile can shed senior/unrelated roles by
  reasoning over the full posting, opt-in and per-profile.
- Net cost-negative on noisy profiles: 1 cheap call replaces N x 2 expensive ones.
- Reuses an existing, proven shape (ADR-060 wide-net-then-narrow) — minimal new
  surface, no `interrupt()`.

### Tradeoffs

- One added sequential cheap call before scoring (~1-2s latency) on enabled runs.
- An LLM filter can mis-drop. Mitigated by the conservative "keep when unsure"
  prompt, the recorded per-job reason, and the keep-all fallback on any failure.
- Discovery widens to the manual-mode net when enabled, so scraping does more work
  (no LLM cost there; bounded by `MAX_DISCOVERED_JOBS`).

### Neutral

- Docs: ADR-079 + index, CLAUDE.md (scraper/auto-selection rules + new agent row),
  `config_model.md` (`search.relevance_filter`), `workflow_model.md` (the new node +
  gate), `agent_model.md` (the new agent contract), `config.example.yaml`. New
  prompt file. Model assignment pinned in `tests/model_pins.json` (Haiku) with the
  pin updated in a separate commit after a live inspection (ADR-058 gate).
- Tests: agent schema construction; node keep/drop narrowing + keep-all fallback;
  `scoring_mode_gate` three-way routing; `get_max_discovered_jobs` widening when
  enabled; PII-redaction invariant still passes (new `resume_profile` context site
  goes through `trim_resume_profile`).

## References

- ADR-060 / ADR-061 — Manual scoring selection + configurable funnel width (the
  wide-net-then-narrow shape this automates).
- ADR-064 / ADR-065 — Per-profile search criteria + experience-targeted discovery
  (the deterministic filters this reasoning layer sits on top of).
- ADR-069 — PII redaction at the LLM seam (the profile-trimming this reuses).
- ADR-059 — HITL retirement (no `interrupt()`; this stays out-of-pause).
- ADR-053 / ADR-058 — Per-agent model assignment via ModelRegistry + the pin gate.
