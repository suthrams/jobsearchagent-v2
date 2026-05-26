"""FastAPI application entry point for Job Search Agent v2.

Start with: uvicorn app.api.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()  # load .env before any os.environ reads (e.g. ANTHROPIC_API_KEY)

from app.api.dependencies import build_and_cache_graph, cleanup_graph, get_graph
from app.api.routers.config import router as config_router
from app.api.routers.jobs import exclusion_router as jobs_exclusion_router
from app.api.routers.jobs import router as jobs_router
from app.api.routers.reports import router as reports_router
from app.api.routers.tailoring import router as tailoring_router
from app.api.routers.users import router as users_router
from app.api.routers.workflows import router as workflows_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Skip if a test has already injected a graph via dependency_overrides
    if get_graph not in app.dependency_overrides:
        build_and_cache_graph()
    yield
    cleanup_graph()


app = FastAPI(title="Job Search Agent v2", lifespan=lifespan)


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

app.include_router(workflows_router)
app.include_router(jobs_router)
app.include_router(jobs_exclusion_router)  # ADR-057: per-job exclusion endpoints
app.include_router(reports_router)
app.include_router(config_router)
app.include_router(tailoring_router)
app.include_router(users_router)  # ADR-062: profile management
