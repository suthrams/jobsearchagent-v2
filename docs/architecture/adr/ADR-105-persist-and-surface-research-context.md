# ADR-105: Persist and Surface the Research Agent's Output

## Status

- **Accepted** (2026-06-13). Surfaced by user observation: "I have never visibly
  observed what the research agent did."
- Builds on ADR-075 (UI reads through the API) and the per-job pipeline read; sibling
  to the persisted score / review / advice / prep outputs.

## Context

- The Research Agent (bounded ReAct, runs before scoring on every job) returns a rich
  `ResearchContext`: `company_summary`, `role_context`, `technology/leadership/domain_signals`,
  `risk_flags`, the ReAct `research_steps` (tool used + observation), and `confidence`.
- In `scoring_runner.score_one_job` it is produced, passed to the scoring agent as
  `research_context` (so it SHAPES the score), then **discarded** — only `score.model_dump()`
  is persisted to `job_scores`. The research output is never written, read, or rendered.
- The only trace today is one `agent_events` row whose `output_summary` is COUNTS only
  (`tech_signals=N risk_flags=M confidence=X`) + a duration, visible in the Live monitor.
- So a load-bearing input to every score is invisible. Not a missed panel — the content
  genuinely is not persisted.

## Decision

Persist the research output 1:1 with the score, and surface it on both the per-job
**Opportunity** page and the run-level **Search detail** (Workflow Detail) page.

### Persistence (Option A — chosen)

- Add a nullable `research_context_json TEXT` column to `job_scores` (the score and its
  research are 1:1; written in the SAME persist step that already writes the score).
  Idempotent `ALTER TABLE ... ADD COLUMN` migration + the `_SCHEMA_SQL` CREATE, the
  established pattern for ADR-057/080/100 columns.
- `ScoreRepository.create(..., research_context: dict | None = None)` stores
  `json.dumps(research_context)` (or `NULL`). `scoring_runner.score_one_job` passes
  `research.model_dump()`. Back-compat: the param defaults to `None`, so old call sites
  and old rows read back as "no research".
- **Rejected B (new `research_contexts` table):** over-normalized for a 1:1 relationship.
  **Rejected C (fold into `score_json`):** mixes two agents' outputs into one schema and
  muddies the score contract; a separate column keeps the boundary clean.

### Reads

- **Opportunity (per job):** `get_job_pipeline` selects the new column and returns
  `out["research"] = {"data": <parsed>, "created_at": ...}` alongside `score`/`review`/etc.
- **Search detail (per run):** new `list_research_contexts(workflow_id)` read +
  `GET /workflows/{id}/research`, parallel to the existing `list_deep_review_results` /
  `list_interview_prep` reads — returns each scored job's `{job_id, title, company, research}`.
  Chosen over piggybacking on `list_workflow_jobs` so the scored-jobs table payload (which
  feeds a dataframe) is not bloated with a multi-KB blob per row.

### UI

- Shared `app/ui/components/research_panel.py::render_research(research)` — company
  summary + role context, signal chips (tech/leadership/domain), risk flags, the ReAct
  step trace (step / tool / observation), and confidence. Tolerant of missing/empty fields.
- **Opportunity:** a "What the research agent found" section from `pipeline["research"]`.
- **Search detail:** a collapsed "Research findings (N)" expander iterating the run's
  research, each job rendered with the shared panel.

## Boundaries / non-goals

- **Display only; does not change scoring.** The research already fed the score; this only
  stops throwing the evidence away. No agent/prompt/limit change.
- **No backfill.** Runs scored before this ADR have `NULL` research and render "no research
  recorded" — we do not re-run research for historical jobs (cost).
- **PII / untrusted-input posture unchanged.** `research_steps` already stores observation
  SUMMARIES, never raw chain-of-thought (schema docstring); the agent reads untrusted job
  text under the standing guardrails. Persisting the structured output adds no new identifier
  surface beyond what the score already carries.

## PSSR

- **Performance/Scalability:** one extra TEXT column + one extra small read; the per-run
  read is bounded by `MAX_JOBS_PER_RUN`. No new LLM call (data already computed) -> no cost.
- **Security:** PII-safe by construction (the schema is signals/summaries, not resume
  content or identifiers); reads go through the API like every other funnel read (ADR-075).
- **Reliability:** nullable column + `None`-tolerant create + try/except blob parse =
  back-compatible on old DBs and old rows; a parse failure degrades to "no research".

## Tests

- `ScoreRepository.create` round-trips research; `get_job_pipeline` returns `out["research"]`.
- `list_research_contexts` returns per-job research for a run; empty when none stored.
- Back-compat: a score created without research reads back with `research=None`.

## References

- ADR-075 (UI reads through the API), `agent_model.md` (Research Agent contract),
  `data_model.md` (`job_scores`), `app/schemas/research_context.py`.
