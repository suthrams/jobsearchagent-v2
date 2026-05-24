# ADR-061: Configurable funnel width + on-demand deep review and interview prep

## Status

Accepted (2026-05-24).

Relates to ADR-052 (reduce MAX_JOBS_PER_RUN as a cost control), ADR-054 (every
qualifying job reaches deep review), ADR-055 (on-demand tailoring as an
out-of-graph operation), ADR-059 (retire in-graph HITL; reserve interrupt-before
for irreversible actions), and ADR-060 (human triage before scoring). It widens
the levers ADR-052/ADR-060 introduced and makes them user-owned, and it extends
ADR-055's out-of-graph operation pattern to two more agents.

## Context

The job-processing funnel narrows left to right:

```
discover -> score -> auto-select (top 3) -> deep review -> tailor -> fidelity -> interview
```

Two properties of the funnel were hard-coded as cost controls:

1. **Discovery and scoring width are fixed constants.** `MAX_JOBS_PER_RUN = 10`
   bounds both how many jobs are discovered (auto mode) and how many are scored;
   `MAX_DISCOVERED_JOBS = 50` is the manual-mode (ADR-060) wide net. Neither is
   user-configurable - a user who wants to cast wider or pay to score more than
   10 cannot, even when they accept the cost.

2. **Only auto-selected jobs can be tailored or interview-prepped.** Tailoring
   (ADR-055) is already an out-of-graph on-demand operation, but the UI only
   offers it for the auto-selected top-3 deep-reviewed jobs (`selected_jobs`).
   Interview prep runs only automatically inside the graph (threshold-gated) and
   has no on-demand trigger, even though the `user_requested_interview_prep`
   state flag and the InterviewCoach agent already exist.

The user's mental model is a funnel they steer: cast as wide as they choose,
score as many as they judge worth it, and then push *any* scored job - not just
the three the system auto-picked - through tailoring and interview prep. The
narrowing should be a human decision at each stage, bounded by a cost ceiling,
not a fixed cap that decides for them.

This must hold the line ADR-059 drew: the graph runs end to end with no
`interrupt()`. Any new human-driven step is modelled out-of-graph (ADR-055's
shape), and any widened cap is still backstopped by `MAX_LLM_CALLS_PER_RUN`.

## Decision

### A. Discovery and scoring width become configurable (system-wide + per-run), with hard ceilings

Two config keys, merged the standard three-tier way (`config.yaml` defaults ->
`user_config` system-wide overrides -> per-run `effective_config`):

- `scoring.max_scored` - how many jobs get scored. Default `10`
  (`MAX_JOBS_PER_RUN`), hard ceiling `MAX_SCORED_CEILING = 25`. Governs both
  auto-mode scoring (discovery == scoring there) and the ADR-060 manual-mode
  phase-2 selection cap.
- `search.max_discovered` - the manual-mode (ADR-060) wide discovery net.
  Default and hard ceiling `MAX_DISCOVERED_JOBS = 50`.

Cost safety is enforced in **two** places, deliberately:

1. `app/workflows/limits.py` helpers (`get_max_scored`, `get_max_discovered_jobs`)
   clamp to the ceiling. This is the authoritative workflow gate - it sees the
   per-run `effective_config`, which can arrive un-clamped because the UI builds
   it directly (it does not pass through ConfigService).
2. `ConfigService._enforce_limits` clamps the merged system-wide config, the
   same mechanism that already clamps `search.max_jobs` to `_SYSTEM_MAX_JOBS`.
   This keeps the value the Settings UI displays and any inherited config clean.

`MAX_LLM_CALLS_PER_RUN = 200` remains the absolute backstop and a protected key.
At the new ceiling, scoring 25 jobs costs at most 50 LLM calls (research +
scoring), well inside the run budget.

### B. Ad-hoc tailoring for any scored job, with deep-review-on-demand first

- The UI tailoring picker widens from `selected_jobs` (auto-selected top-3) to
  `scored_jobs` (any job that was scored). The user picks any scored job.
- A scored job that was never deep-reviewed has no critic/auditor context for the
  TailoringAgent. So tailoring a not-yet-reviewed job runs the
  ResumeCritic+ReviewAuditor reflection loop **for that one job first**, then
  tailors. This is the "deep-review-on-demand" behaviour.
- To avoid duplicating the ~100-line reflection loop, the single-job loop is
  extracted from the `deep_review` node into a shared
  `app/services/deep_review_runner.py::review_one_job`. The graph node and the
  on-demand endpoint call identical code.
- New endpoint `POST /workflows/{wf}/jobs/{job}/deep-review` runs the loop for
  one job out-of-graph and persists via `ReviewRepository`. The tailoring
  endpoint gains `auto_deep_review` (default true): if no review row exists for
  (wf, job), it runs the loop before tailoring.

### C. On-demand interview prep

- New endpoint `POST /workflows/{wf}/jobs/{job}/interview-prep` runs the
  InterviewCoach for one chosen job out-of-graph, sourcing career-advice and
  final-review context from the repos (as the tailoring endpoint already does),
  and persists via `AdviceRepository.create_prep`.
- A per-job "Prep for interview" button in Workflow Detail triggers it. The
  automatic, threshold-gated in-graph interview prep is unchanged.

All three new operations are out-of-graph and synchronous, exactly like ADR-055
tailoring. No `interrupt()` is introduced; ADR-059's property stands.

## Options considered

- **Configurable caps with a hard ceiling (chosen for A).** User owns the width
  within a cost-safe envelope. Alternative: no ceiling (only the run budget
  backstop) - rejected as too easy to trigger a surprise-cost run. Alternative:
  just raise the fixed constants - rejected because it is not user-configurable
  and bakes one person's cost appetite into the code.
- **Deep-review-on-demand before ad-hoc tailoring (chosen for B).** Alternative:
  let the user tailor a scored-but-unreviewed job with empty review context -
  cheaper but lower-quality tailoring. Alternative: keep tailoring gated on the
  auto-selected top-3 - preserves the old discipline but does not match the
  user-steered-funnel intent. The chosen option keeps tailoring quality high
  while letting the human, not the top-3 auto-select, decide what to tailor.
- **Out-of-graph endpoints (chosen for B and C).** Mirrors ADR-055; preserves
  ADR-059. The in-graph alternative would reintroduce interrupt/resume.

## Consequences

### Positive

- The funnel width is a human decision at every stage, bounded by a cost ceiling
  rather than a fixed cap.
- Any scored job can be carried all the way to a tailored, fidelity-reviewed
  resume and an interview plan - not only the three the system auto-selected.
- Backwards compatible: defaults reproduce today's behaviour
  (`max_scored=10`, `max_discovered=50`, tailoring still available for selected
  jobs, interview prep still runs automatically).

### Tradeoffs

- Raising `scoring.max_scored` toward 25 raises per-run cost roughly linearly.
  The ceiling and the run budget bound the worst case; the cost is the user's
  explicit choice.
- Ad-hoc tailoring of a not-yet-reviewed job now costs a deep-review loop
  (up to `MAX_REVIEW_ROUNDS * 2` calls) before the tailoring + fidelity calls.
  Surfaced in the UI so the cost is not a surprise.
- Two more out-of-graph operation paths to maintain and test.

### Neutral

- New config keys (`scoring.max_scored`, `search.max_discovered`) and ceiling
  constants (`MAX_SCORED_CEILING`) to document in `config_model.md`, the
  `api_reference.md` (two new endpoints), and `CLAUDE.md` invariants.
- The single-job reflection loop moves to a shared service; the `deep_review`
  node becomes a thin fan-out over it (no behaviour change).

## References

- ADR-052 - Reduce MAX_JOBS_PER_RUN as a cost control (the cap this makes configurable).
- ADR-054 - Allow deep review for all qualifying jobs.
- ADR-055 - On-demand tailoring as an out-of-graph operation (the shape reused for B and C).
- ADR-059 - Retire in-graph HITL; reserve interrupt-before for irreversible actions (the property preserved).
- ADR-060 - Human triage before scoring (the manual-selection mode whose wide net becomes configurable).
