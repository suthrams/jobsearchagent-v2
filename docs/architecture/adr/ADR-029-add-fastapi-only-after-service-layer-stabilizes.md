# ADR-029: Include FastAPI as the Backend API Layer

## Status
Accepted (Revised)

## Context
The original decision deferred FastAPI until the service layer stabilized. However, since LangGraph and LangChain are now the orchestration foundation (ADR-001 revised), the workflow service boundaries are well-defined by the architecture documents. FastAPI can be introduced as part of the initial v2 build rather than deferred.

## Decision
Include FastAPI as the backend API layer in v2 from the start.

FastAPI will expose workflow services to the Streamlit UI and serve as the boundary between the frontend and the backend orchestration layer.

## Rationale
The workflow contracts are clearly defined in the architecture docs (workflow_model.md, state_and_memory_model.md). There is no risk of locking in unstable contracts. FastAPI also enforces the principle that the UI calls services through an API rather than directly embedding workflow logic — which is the correct separation defined in ADR-003 and ADR-004.

## Consequences

### Positive
- Clean separation between UI and backend from the start
- Streamlit calls FastAPI endpoints, not workflow functions directly
- Enables future non-Streamlit frontends without backend changes
- FastAPI's Pydantic integration aligns with the structured output schema approach

### Tradeoffs
- Slightly more initial setup than direct service calls
- Requires running FastAPI server alongside Streamlit

## Implementation Notes
- FastAPI app lives in `app/api/`
- Endpoints map to workflow entry points (start_workflow, submit_decision, get_status, etc.)
- Streamlit calls FastAPI via HTTP — no direct imports of workflow internals
- Celery, Redis, and background task queues are excluded — not needed for MVP scope
- SQLAlchemy is excluded — raw sqlite3 is sufficient
