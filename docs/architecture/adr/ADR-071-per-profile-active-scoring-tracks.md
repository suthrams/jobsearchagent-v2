# ADR-071: Per-Profile Active Scoring Tracks

## Status

Accepted (2026-06-01). Implemented.

Extends ADR-062 (multi-user profiles — the per-profile config layer this lives in)
and refines ADR-054/ADR-061 (the deep-review qualification rule this narrows).

## Context

The system scores every job across three career tracks and persists all of them:

- `technical_score`  (track `ic`)
- `architecture_score` (track `architect`)
- `leadership_score` (track `management`)

Plus `overall_score` and `domain_score`. The Scoring Agent prompt explicitly
instructs "always populate all four numeric dimensions regardless of track"
(`scoring_agent.txt` v1), and the deep-review gate qualifies a job when **ANY** of
the three track scores clears the threshold
(`qualifies_for_deep_review()` / `best_track_score()` in `app/workflows/limits.py`).

This three-track model fits the Primary profile, whose owner genuinely spans IC,
architecture, and management. It does **not** fit most profiles. A new profile is
usually one or two tracks — an early-career security analyst is IC-only; a
hands-on staff engineer is IC + architect; few are all three.

Scoring all three for a single-track profile is not just noise. It is a
correctness problem: `qualifies_for_deep_review` fires on *any* track, so a
spuriously high `leadership_score` on an IC-only profile can push a job into a
paid deep-review + interview-prep pass that the human never wanted. The
irrelevant tracks also clutter the Analytics, Workflow Detail, and Job Detail
screens with columns that mean nothing for that profile.

The existing `scoring.career_track` knob (`ic` / `architect` / `management` /
`all`) only nudges the prompt's *weighting commentary*. It does not restrict which
tracks are scored, nor which count toward qualification. There is no per-profile
way to say "this profile only has these tracks."

## Decision

Add a per-profile **active tracks** set: the subset of the fixed three tracks that
a profile is actually pursuing. Inactive tracks are not scored, do not count toward
deep-review qualification, and are not shown in the UI. Default is all three, so
the Primary profile and every existing run are unchanged.

The three tracks stay fixed (`ic` / `architect` / `management`) — this is a
*subset selection*, not a custom-track system. The canonical track -> score-field
map is:

```
ic         -> technical_score
architect  -> architecture_score
management -> leadership_score
```

### A. Config knob (per-profile, ADR-062 layer)

New `scoring.tracks: list[str]`, a subset of `["ic", "architect", "management"]`.
Default (absent / empty / all-invalid) = all three. Stored per profile in
`user_config` (not protected — users set it), merged into `effective_config` by
`ConfigService`, and clamped in `_enforce_limits` (drop unknown names; an empty
result is removed so the all-three default applies). It is **not** added to
`_PROTECTED_KEYS`.

A state-aware reader `get_active_tracks(state)` in `app/workflows/limits.py` is the
single source of truth for the active set (mirrors `get_max_scored` etc.): it
reads `effective_config.scoring.tracks`, validates against `VALID_TRACKS`, and
returns all three when nothing valid is set. `active_track_keys(state)` maps that
to the score-field tuple. Per the existing "never inline the comparison" rule
(CLAUDE.md), callers read these helpers — they do not inline the track set.

### B. Scoring scope — inactive tracks are not scored

`JobScore` (`app/schemas/job_score.py`) makes the three track fields optional:
`technical_score` / `architecture_score` / `leadership_score` become
`int | None = None`. `overall_score` and `domain_score` stay required. Because the
Scoring Agent uses `JobScore` directly as its structured-output schema, optional
fields let the model legitimately omit an inactive track rather than inventing a
score for it.

`score_jobs` passes `active_tracks` (from `get_active_tracks(state)`) into the
scoring context. The prompt (`scoring_agent.txt` -> v2) is told to score **only**
the active tracks, set the others to `null`, and compute `overall_score` across the
active set only, keeping the `career_track` emphasis among the active tracks.

### C. Qualification scope — inactive tracks do not gate

`best_track_score(scored_job, active_keys=...)` and
`qualifies_for_deep_review(scored_job, threshold, active_keys=...)` take the active
score-key tuple, defaulting to all three for backward compatibility. Every caller
that gates on track scores passes the run's active keys:

- `await_job_selection` (auto-select),
- `routers.py::deep_review_gate`,
- `interview_prep` node,
- `constraint_analyzer` (no-qualifying-jobs diagnostics),
- the out-of-graph `deep_review_runner` (on-demand deep review / tailoring
  auto-deep-review).

A `None` (unscored) track is treated as 0 and, being outside `active_keys`, never
qualifies a job on its own — which is the whole point.

### D. UI

- **Settings**: a `st.multiselect` for `scoring.tracks`, per profile (default all
  three).
- **Start New Run**: the per-run `effective_config` inherits the profile's
  `tracks` (injected in `start_run.py`, replacing the hardcoded
  `career_track: "all"` block). No new per-run widget — a run uses its profile's
  tracks. (A per-run override remains a cheap future addition if needed.)
- **Analytics / Workflow Detail / Job Detail**: render only the active tracks.
  The active set is read from the run's stored `effective_config`
  (`register_run` already persists it to `workflow_runs.state_json`); the fallback
  when a run predates this ADR is to hide columns whose values are all null.

### Relationship to `career_track`

`scoring.career_track` is retained unchanged as the *emphasis* hint (it weights
`overall_score` and feeds the Career Advisor). `scoring.tracks` is the orthogonal
*inclusion* set. If `career_track` is set to a track not in `tracks`, it is
ignored (the active set wins). They are not merged into one knob to avoid
overloading a field whose existing meaning (weighting) differs from the new one
(inclusion).

### Persistence

No schema migration. The three track scores live inside the `score_json` blob
(only `overall_score` is a denormalized column), so making them optional is free at
the persistence layer. Existing `job_scores` rows keep all three populated. The
per-run active set is recoverable from `workflow_runs.state_json.effective_config`.

## Options considered

- **Subset of the fixed three tracks (chosen).** Smallest coherent change; schema,
  UI labels, and the track -> field map stay stable. Covers the actual need
  ("1 or 2 of the existing 3").
- **Fully custom, profile-defined tracks.** Rejected — variable-width schema,
  dynamic prompt and UI, far more surface, and not what the requirement asks for.
- **Keep scoring all three, exclude inactive only from the gate + UI.** Rejected as
  the default behavior — it still generates and stores a phantom score for an
  irrelevant track, which is dishonest and re-leaks into any future caller that
  forgets the active-keys argument. Scoring only the active tracks makes the
  data itself say what is true.
- **Fold `tracks` into `career_track`.** Rejected — `career_track` already means
  "weighting emphasis"; overloading it to also mean "inclusion set" would conflate
  two distinct concerns and break the `all` semantics.
- **Auto-derive the active tracks from the resume/role via an LLM.** Deferred — a
  per-profile LLM inference at setup is less predictable and adds cost; a manual
  setting is the right v1. Auto-suggest-with-override can layer on later without
  changing this contract.

## Consequences

### Positive

- A single- or dual-track profile no longer pays for, qualifies on, or sees
  irrelevant track scores. Deep-review selection reflects the tracks the profile
  actually pursues.
- Slightly lower output-token cost (the model emits fewer numeric fields).
- The persisted score data is honest: an unscored track is `null`, not a fabricated
  number.

### Tradeoffs

- The three track score fields are now nullable; any future consumer must tolerate
  `None`. Mitigated by the None-tolerant helpers and a source-scan-style test of
  the qualification path.
- Two related knobs (`tracks` inclusion vs `career_track` emphasis) coexist; the
  ADR and CLAUDE.md document the split to prevent confusion.

### Neutral

- Default = all three keeps the Primary profile and every pre-existing run
  byte-for-byte unchanged; no migration.
- Docs: ADR-071 + index, CLAUDE.md (Auto-selection rules + a new Scoring-tracks
  invariant), `data_model.md` (effective_config scoring block),
  `config_model.md` (`scoring.tracks`), `agent_model.md` (Scoring Agent output),
  `workflow_model.md`, `config.example.yaml` / `config.yaml`. Tests: schema
  accepts `None`; `get_active_tracks` default/clamp/override; the core
  correctness case (a job clearing only an inactive track does not qualify);
  `best_track_score` honoring active keys; `score_jobs` passing `active_tracks`;
  config flow; backward-compat (profile `"0"` -> all three); UI smoke with one
  active track.

## References

- ADR-062 — Multi-user profiles (the per-profile config layer this knob lives in).
- ADR-061 — Configurable funnel width (the scoring caps this sits beside).
- ADR-054 — Allow deep review for all qualifying jobs (the qualification rule this
  narrows to the active tracks).
- ADR-064 / ADR-065 — Per-profile search criteria / experience targeting (the same
  opt-in, default-off, per-profile pattern).
