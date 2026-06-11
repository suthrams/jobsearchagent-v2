# ADR-100: "Review Later" List + On-Demand Rescue-to-Score

## Status

**Accepted** (2026-06-11) — **Phases 1 and 2 implemented.** Follows the
relevance-filter dropped-job link work (BUG-010 follow-on) and the ADR-079
suitability recalibration. Builds on the favorites machinery (ADR-090) and the
on-demand op pattern (ADR-061). Phase 2 endpoint: `POST
/workflows/{wf}/jobs/{job}/score`, backed by the extracted
`scoring_runner.score_one_job` shared with the `score_jobs` node.

## Context

The relevance pre-filter (ADR-079) and the deterministic clearance filter (ADR-094)
hard-drop jobs **before** scoring. The drop is now auditable: the "Why N jobs were
filtered out" panel lists each dropped job with its reason and a click-through link,
so a user can review a verdict and disagree with it.

What is missing is a way to **act** on that disagreement. A dropped job the user
wants to keep is stranded in the discard audit — there is no bucket to set it aside
for later, and no path to send it through scoring after the run has completed. The
user's stated need: review a dropped job, decide "maybe — keep it for future
consideration," and *later* (on their own time) push it into scoring so it follows
the regular route (research -> score -> Matches -> deep-review / tailoring /
interview-prep).

Two existing seams make this cheap:
- **Favorites (ADR-090)** is already a bounded, per-profile, purge-surviving saved-job
  store that is explicitly a FILTER-INPUT (curation), NOT application tracking (no
  status/applied/stage/outcome field, guarded by a forcing-function test). A
  "Review later" list is the same data shape with different semantics.
- **On-demand ops (ADR-061)** already run agents out-of-graph on a single job
  (`deep_review_runner.review_one_job`, interview-prep), persisting via repos and
  never mutating the completed run's checkpointer state. Scoring one job on demand is
  the same pattern, and the per-job research+score+persist logic already exists inside
  the `score_jobs` node (`_score_one`), ready to extract into a shared runner exactly
  as `review_one_job` was.

## Decision

Add a per-profile **"Maybe / Review later"** list and an **on-demand rescue-to-score**
path, in two phases.

### Phase 1 — the Review-later list (no scoring, no cost)

- **A `review_later` saved-job kind.** Generalize the `favorite_jobs` store into a
  kind-discriminated saved-jobs store (`kind in {favorite, review_later}`), so both
  lists share ONE tested seam: the no-status invariant, the per-profile cap, run-purge
  survival, and `?user_id=` scoping. (One seam over two near-identical tables — the
  BUG-011 lesson that two stores handling the same shape differently will drift.) The
  `review_later` kind keeps its own cap and its own list view; favorites behaviour is
  unchanged and its forcing-function no-status test extends to cover both kinds.
- **"Move to Review later" action** on each row of the "Why filtered out" panel
  (Workflow Detail). The dropped job is already in the `jobs` table, so this is a pure
  DB write of a snapshot (title/company/url/source) under `kind=review_later`. No
  agent, no LLM, no run-state mutation. The panel cross-references at read time: a
  dropped `job_id` already on the list renders as "On your Review-later list" instead
  of the move button.
- **A "Review later" view** (per-profile, reachable from nav), listing saved jobs
  with their link and source, newest first — the standing "consider later" bucket.

### Phase 2 — on-demand rescue-to-score from the list

- **Extract `score_one_job(...)`** into `app/services/scoring_runner.py` from
  `score_jobs._score_one` (research -> score -> persist via `ScoreRepository`). The
  `score_jobs` node is refactored to call it, so the in-graph batch and the on-demand
  single-job path run identical logic (mirrors `deep_review_runner`).
- **`POST /workflows/{wf}/jobs/{job}/score`** (on-demand, synchronous; ADR-061
  pattern): require `resume_profile`; idempotent (if already scored for the run,
  return the existing score, no re-spend); load the job via `_find_job` (its
  jobs-table fallback already handles dropped jobs); call `score_one_job`. The scored
  job then surfaces in Matches (that list reads from `ScoreRepository`, not run state)
  and becomes eligible for every existing on-demand op — i.e. it joins the **regular
  route**.
- **A "Score this job" button** on each Review-later row triggers it; on success the
  row links through to the job in Matches.

## Boundaries (what this is NOT)

- **Not application tracking.** The Review-later list never gains a status / applied /
  pursuing / stage / outcome field — the same line favorites holds (ADR-090,
  filter-vs-tracker). It is pre-decision curation the user owns, not a record of an
  application decision. The favorites no-status forcing-function test is extended to
  assert it for every saved-job kind.
- **Not auto-anything.** Moving a job to Review-later costs nothing and triggers no
  agent. Scoring happens only when the user explicitly asks, one job at a time, and is
  not bounded by `scoring.max_scored` (an explicit single-job op, like on-demand
  deep-review).
- **No new graph state / no interrupt.** Both phases are out-of-graph and persist via
  repos; the completed run's checkpointer state is never patched.

## Consequences

- **Positive:** closes the loop opened by the dropped-job links — a reviewed-and-kept
  job has somewhere to go and a path back into scoring. Reuses two proven seams
  (favorites store, on-demand op) and extracts a `score_one_job` runner that removes
  duplication between the node and the new endpoint.
- **Cost (PSSR):** Phase 1 is DB-only. Phase 2 is ~2 LLM calls per rescue,
  user-initiated, logged via the existing `llm_calls` telemetry; never batched, never
  automatic.
- **Migration:** adding `kind` to the favorites store is a backward-compatible
  column (default `favorite`); existing favorites are untouched.
- **Reversibility:** additive (new list + buttons + one endpoint); easy to remove.
- **Negative / risk:** generalizing a shipped feature (favorites) carries refactor
  risk; mitigated by keeping favorites' public behaviour and tests intact and
  extending them to the new kind. If the migration risk is judged too high, the
  fallback is a parallel `review_later_jobs` table — explicitly the lesser option
  (duplication that can drift).
