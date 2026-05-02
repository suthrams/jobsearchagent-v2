# ADR-047: Use SqliteSaver for LangGraph Workflow Checkpoint Persistence

## Status
Accepted

## Context
Phase 7 introduced live agent execution. HITL workflows pause mid-run and must survive process restarts — `pending_decision` state written before the pause must still be present when the backend resumes. LangGraph ships two checkpointers: `MemorySaver` (in-process dict, lost on restart) and `SqliteSaver` (durable SQLite file).

## Decision
Use `SqliteSaver` (writing to `data/v2.db`) in live-agent mode. Retain `MemorySaver` in mock mode (no ANTHROPIC_API_KEY set).

## Rationale
- SqliteSaver provides durable HITL pause/resume without adding new infrastructure (no Redis, no Postgres, no external service).
- Aligns with ADR-028 (SQLite as the MVP persistence tier).
- MemorySaver is fast and correct for tests and mock mode where durability is not required.
- The live/mock gate in `app/api/dependencies.py` makes the swap transparent to all callers.

## Consequences

### Positive
- Workflow state survives backend restarts between HITL pause and resume
- No additional infrastructure dependency
- SqliteSaver shares the same database file as the rest of the v2 schema

### Tradeoffs
- SqliteSaver uses its own internal schema — LangGraph checkpoint tables coexist with v2 application tables in the same file
- SqliteSaver is a context manager; lifecycle must be managed in the FastAPI lifespan hook

## Implementation Notes
- `build_and_cache_graph()` in `app/api/dependencies.py` enters the SqliteSaver context manager at startup and exits it in the lifespan teardown
- `MemorySaver` is used in `_build_mocked_deps()` so the test suite never touches SqliteSaver
