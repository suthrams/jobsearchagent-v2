"""FastAPI application entry point for Job Search Agent v2.

Start with: uvicorn app.api.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import build_and_cache_graph
from app.api.routers.jobs import router as jobs_router
from app.api.routers.reports import router as reports_router
from app.api.routers.workflows import router as workflows_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    build_and_cache_graph()
    yield


app = FastAPI(title="Job Search Agent v2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows_router)
app.include_router(jobs_router)
app.include_router(reports_router)
