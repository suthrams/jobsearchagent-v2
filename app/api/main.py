"""FastAPI application entry point for Job Search Agent v2.

Start with: uvicorn app.api.main:app --reload
"""
# load_dotenv() must run before the app/router imports below (they transitively
# read env vars like ANTHROPIC_API_KEY at import time), so those imports are
# intentionally not at the top of the file. E402 is suppressed file-wide for that.
# ruff: noqa: E402
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()  # load .env before any os.environ reads (e.g. ANTHROPIC_API_KEY)

from app.api.dependencies import build_and_cache_graph, cleanup_graph, get_graph
from app.api.routers.admin import router as admin_router
from app.api.routers.config import router as config_router
from app.api.routers.favorites import router as favorites_router
from app.api.routers.dashboard import router as dashboard_router
from app.api.routers.health import router as health_router
from app.api.routers.reads import router as reads_router
from app.api.routers.jobs import exclusion_router as jobs_exclusion_router
from app.api.routers.jobs import router as jobs_router
from app.api.routers.reports import router as reports_router
from app.api.routers.resume_clinic import router as resume_clinic_router
from app.api.routers.review_later import router as review_later_router
from app.api.routers.tailoring import router as tailoring_router
from app.api.routers.users import router as users_router
from app.api.routers.workflows import router as workflows_router
from app.services.observability_service import record_api_request_safe


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Skip if a test has already injected a graph via dependency_overrides
    if get_graph not in app.dependency_overrides:
        build_and_cache_graph()
        _recover_orphaned_runs_on_startup()
    yield
    if get_graph not in app.dependency_overrides:
        _drain_inflight_runs_on_shutdown()
    cleanup_graph()


# ADR-096: how long a graceful shutdown waits for in-flight runs to checkpoint
# before stopping the pool. Bounded so --reload / deploys don't hang; runs that
# exceed it are picked up by startup recovery on the next boot. Env-overridable.
def _drain_timeout_seconds() -> float:
    import os
    try:
        return max(0.0, float(os.environ.get("WORKFLOW_SHUTDOWN_DRAIN_SECONDS", "30")))
    except (TypeError, ValueError):
        return 30.0


def _recover_orphaned_runs_on_startup() -> None:
    """Resume (or fail) runs left running/cancelling by a previous, now-dead process
    (ADR-096 Layer 2).

    A workflow runs in an in-process thread pool, so a server restart or crash
    mid-run (incl. the 'cannot schedule new futures after interpreter shutdown' seen
    when uvicorn is stopped while a run executes) freezes its row at running/
    cancelling. At startup the executor + run_control registry are empty, so any such
    row is definitively orphaned. recover_orphaned_runs resumes each one from its
    SqliteSaver checkpoint (under an attempt cap) and fails the unrecoverable ones.
    Best-effort: never block startup on a recovery failure.
    """
    import logging

    from app.api.routers.workflows import recover_orphaned_runs

    try:
        result = recover_orphaned_runs(get_graph())
        if result["resumed"] or result["failed"]:
            logging.getLogger(__name__).warning(
                "Startup run recovery: resumed=%s failed=%s",
                result["resumed"], result["failed"],
            )
    except Exception:  # noqa: BLE001 - recovery must never block startup
        logging.getLogger(__name__).exception(
            "Startup run recovery failed (continuing).")


def _drain_inflight_runs_on_shutdown() -> None:
    """Let in-flight runs reach a checkpoint before the process exits (ADR-096
    Layer 1), so a graceful shutdown doesn't guillotine them mid-node. Bounded +
    best-effort; whatever doesn't finish is recovered on the next startup."""
    import logging

    from app.api.routers.workflows import drain_inflight_runs

    try:
        finished, still_running = drain_inflight_runs(_drain_timeout_seconds())
        if finished or still_running:
            logging.getLogger(__name__).warning(
                "Shutdown drain: %d run(s) finished, %d still running "
                "(will be recovered on next startup).", finished, still_running,
            )
    except Exception:  # noqa: BLE001 - drain must never block shutdown
        logging.getLogger(__name__).exception(
            "Shutdown drain of in-flight runs failed (continuing).")


app = FastAPI(title="Job Search Agent v2", lifespan=lifespan)

# ADR-084: route templates excluded from api_requests recording (health probes).
_OBSERVABILITY_EXCLUDED = {"/health", "/readyz"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Normalise Pydantic body/path/query validation errors to the same {error, message, details}
    shape that hand-raised HTTPExceptions use across the rest of the API. Without this handler,
    Pydantic's default 422 surface (a list of field errors at the top level) breaks the consumer's
    ability to read errors uniformly."""
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "error": "validation_error",
                "message": "Request failed schema validation.",
                # jsonable_encoder mirrors FastAPI's default handler: a custom
                # validator can put a non-serializable exception in ctx, which a
                # bare exc.errors() would fail to JSON-encode.
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _observe_requests(request: Request, call_next):
    """Record one api_requests row per REST request (ADR-074 Gap 5).

    Captures method, the matched route TEMPLATE (never the raw path/query - PII-safe
    + bounded cardinality), status code, latency, and the acting profile
    (?user_id=, ADR-062). Recording is never-crash and runs in `finally`, so it
    fires even when the handler raises (status 500) and never masks the original
    exception.
    """
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "<unmatched>"
        # ADR-084: health probes are excluded from api_requests - frequent polling
        # would flood the table and a 503 from /readyz must not skew the dashboard's
        # API error rate. Health is a separate signal from request observability.
        if template not in _OBSERVABILITY_EXCLUDED:
            user_id = request.query_params.get("user_id") or "0"
            record_api_request_safe(
                user_id=user_id,
                method=request.method,
                route_template=template,
                status_code=status_code,
                latency_ms=latency_ms,
            )

app.include_router(health_router)  # ADR-084: /health + /readyz (unauthenticated ops)
app.include_router(reads_router)  # ADR-075: before workflows so /workflows/recent matches first
app.include_router(workflows_router)
app.include_router(jobs_router)
app.include_router(jobs_exclusion_router)  # ADR-057: per-job exclusion endpoints
app.include_router(reports_router)
app.include_router(config_router)
app.include_router(tailoring_router)
app.include_router(users_router)  # ADR-062: profile management
app.include_router(favorites_router)  # ADR-090: My favorite jobs
app.include_router(review_later_router)  # ADR-100: Review-later saved-job list
app.include_router(resume_clinic_router)  # ADR-066: standalone Resume Clinic
app.include_router(admin_router)  # ADR-070: data-retention purge
app.include_router(dashboard_router)  # ADR-075: cross-run read endpoints
