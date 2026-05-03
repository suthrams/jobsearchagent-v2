# ADR-054: Allow Deep Review for All Qualifying Jobs

## Status
Accepted

## Context
ADR-012 established that deep review (Resume Critic + Review Auditor + Career Advisor + Interview Coach) runs only on a shortlisted subset of scored jobs. ADR-052 reduced `MAX_JOBS_PER_RUN` to 10 on the assumption that "10 jobs is more than sufficient to find 3 strong matches given the pre-filter gate" — i.e. that `MAX_SELECTED_JOBS = 3` was the right ceiling on deep review.

In practice, runs frequently surface more than 3 jobs that meet the qualifying threshold (any of the three track scores ≥ `min_match_score`, default 75). When that happens, the orchestrator silently drops the 4th-onward qualifiers and the user only learns about it via the Limits & Constraints panel. Users have reported expecting every qualifying job to receive deep review, not just the top 3.

The original `MAX_SELECTED_JOBS = 3` cap pre-dated the ADR-049 concurrent scoring work and the ADR-052 reduction of `MAX_JOBS_PER_RUN` to 10. With those two changes, the upper bound on deep review work is naturally bounded by `MAX_JOBS_PER_RUN`.

## Decision
Raise `MAX_SELECTED_JOBS` from 3 to 10 in `app/workflows/limits.py` so that every qualifying job (any track score ≥ `effective_config.scoring.min_match_score`) proceeds to deep review, up to the discovery cap (`MAX_JOBS_PER_RUN = 10`).

The selection rule itself does not change — `await_job_selection` still filters by `qualifies_for_deep_review` and sorts by `best_track_score` descending. Only the slice cap moves.

## Rationale
- `MAX_JOBS_PER_RUN = 10` is already the effective upper bound. Capping deep review at 3 of those 10 was an arbitrary cost control on top of an existing cost control.
- The threshold (`min_match_score`, default 75) is the right gate for "is this worth deep review" — it is user-tunable and lives in `effective_config`. The arbitrary `MAX_SELECTED_JOBS = 3` cap overrode that signal.
- Concurrent scoring (ADR-049) reduced scoring-phase wall-clock time to the point where deep review is now the dominant cost, so making it threshold-driven instead of slot-driven gives the user better control.

## Consequences

### Positive
- Users get deep review on every job that meets their threshold — no silent truncation when more than 3 qualify.
- The threshold becomes the single, user-tunable knob for deep review volume.
- The 422 error path for "selected too many jobs" is now a 10-job ceiling, matching `MAX_JOBS_PER_RUN`.

### Tradeoffs
- Per-run LLM cost ceiling rises. Worst case (10 jobs, all qualifying, each running the full deep-review chain at ~6–8 LLM calls per job): ~60–80 calls plus scoring/research overhead. `MAX_LLM_CALLS_PER_RUN` was raised from 100 to 200 in this ADR to absorb that without `BudgetExceededError`.
- Estimated worst-case run cost (10 deep-reviewed jobs, no tailoring) lands around $0.15–$0.40 — see README cost table.
- ADR-052's framing — that 10-job discovery is sufficient because only 3 advance — no longer holds. ADR-052 stays accepted as a discovery-side cost lever, but the "3 strong matches" narrative is superseded by this ADR.

### Neutral
- The Pydantic `JobSelectionDecision.selected_job_ids` `max_length` is now bound to `MAX_SELECTED_JOBS` rather than the literal `3`, so future tuning happens in one place.
- The `selected_jobs_cap` finding in `constraint_analyzer.py` now triggers far less often, since cap = discovery cap.

## Implementation Notes
- `app/workflows/limits.py`: `MAX_SELECTED_JOBS = 10`, `MAX_LLM_CALLS_PER_RUN = 200`
- `app/api/schemas/requests.py`: `selected_job_ids: list[str] = Field(min_length=1, max_length=MAX_SELECTED_JOBS)`
- `app/ui/streamlit_app.py`: LLM-calls metric display now derives `/N` from `MAX_LLM_CALLS_PER_RUN` instead of a hardcoded literal
- `config/config.example.yaml`: `limits.max_selected_jobs: 10`, `limits.max_llm_calls_per_run: 200`
- `tests/v2/test_api_workflows.py::test_too_many_jobs_selected` and `tests/v2/test_api_schemas.py` updated to send `MAX_SELECTED_JOBS + 1` IDs
- CLAUDE.md, README.md, wiki.md, features.md, api_reference.md, performance_scalability.md, patterns.md updated to reflect the new values
