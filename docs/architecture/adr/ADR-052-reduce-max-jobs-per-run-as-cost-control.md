# ADR-052: Reduce MAX_JOBS_PER_RUN as the Primary Volume Cost Control Lever

## Status
Accepted (rationale partially superseded by ADR-054 — `MAX_SELECTED_JOBS` is no longer 3, so the "10 jobs is sufficient to find 3 strong matches" framing below no longer applies. The decision to keep `MAX_JOBS_PER_RUN = 10` still stands as a discovery-side cost lever.)

## Context
Phase 9 cost analysis. Research + Scoring cost scales linearly with jobs per run. At MAX_JOBS_PER_RUN = 20, a full run involved 20 Research Agent calls and 20 Scoring Agent calls before deep review even began. Most of the additional jobs beyond the top 3–5 matches never influenced any decision.

## Decision
Reduce `MAX_JOBS_PER_RUN` from 20 to 10 in `app/workflows/limits.py`.

## Rationale
- The workflow's goal is to surface 3 high-match candidates (`MAX_SELECTED_JOBS = 3`) for deep review. 10 jobs is more than sufficient to find 3 strong matches given the pre-filter gate.
- Halves the number of Research + Scoring calls per run — the largest cost contributor.
- `MAX_JOBS_PER_RUN` is the single most effective lever for per-run cost because it multiplies across both Research Agent and Scoring Agent calls.
- The value is defined in one place (`limits.py`) and enforced in the `discover_jobs` node — easy to tune.

## Consequences

### Positive
- Predictable cost ceiling: at most 10 Research + 10 Scoring calls per run
- Faster runs: fewer jobs = lower wall-clock time even with concurrent scoring
- Sufficient coverage: pre-filter gate ensures the 10 jobs surfaced are relevant

### Tradeoffs
- May miss relevant jobs if Adzuna returns more than 10 qualifying results in a single run
- Mitigated by running the agent daily so the discovery window is small

## Implementation Notes
- `app/workflows/limits.py`: `MAX_JOBS_PER_RUN = 10`
- `app/workflows/nodes/discover_jobs.py`: `postings = postings[:MAX_JOBS_PER_RUN]`
- CLAUDE.md invariants table updated to reflect the new value
