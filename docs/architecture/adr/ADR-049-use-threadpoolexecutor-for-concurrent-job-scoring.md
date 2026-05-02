# ADR-049: Use ThreadPoolExecutor for Concurrent Job Scoring

## Status
Accepted

## Context
Phase 8 performance target: reduce wall-clock time for scoring 10 jobs. In sequential execution, each job requires a Research Agent call followed by a Scoring Agent call — both are network IO (LLM API). With 10 jobs this took ~75s serially.

Concurrency options considered:
1. `asyncio` — would require making the entire agent and provider stack async
2. `ThreadPoolExecutor` — works with synchronous code, appropriate for IO-bound work
3. `multiprocessing` — inappropriate for IO-bound network calls, higher overhead
4. External task queue (Celery/Redis) — violates ADR-028 (no external services in MVP)

## Decision
Use `concurrent.futures.ThreadPoolExecutor(max_workers=5)` in the `score_jobs` workflow node. Each worker handles one job's full Research + Scoring cycle concurrently.

## Rationale
- LLM API calls are IO-bound — the Python GIL is not a bottleneck
- No async refactor required in the agent or provider stack
- 5 workers is safe within Anthropic's rate limits while providing ~5× parallelism
- Wall-clock time reduced from ~75s to ~20s for 10 jobs

## Consequences

### Positive
- ~75% wall-clock reduction with no architecture change to agents or providers
- Compatible with synchronous LangChain/LangGraph patterns
- Simple to tune by adjusting `max_workers`

### Tradeoffs
- Metrics must be accumulated atomically across threads — requires `add_llm_calls_bulk()` rather than sequential `add_llm_call()` calls
- Thread-local errors must be caught per-job and not allowed to cancel the pool

## Implementation Notes
- `app/workflows/nodes/score_jobs.py` submits one future per job and collects results
- `add_llm_calls_bulk()` in `app/workflows/limits.py` is the thread-safe metrics accumulator
- Per-job exceptions are caught and recorded as workflow errors without aborting other jobs
