"""Liveness + readiness endpoints (ADR-084).

- GET /health  - liveness. No dependency I/O; always 200 if the process serves.
- GET /readyz  - readiness. Probes shared dependencies (see app/services/readiness.py);
                 200 when ready/degraded, 503 when a critical dependency is down.

Both are unauthenticated and NOT profile-scoped (no ?user_id=, ADR-062 exemption),
and are excluded from api_requests recording in app/api/main.py (probes would flood
the table and a 503 must not skew the dashboard's API error rate). Secret-safe:
the readiness body reports presence/mode only, never key values.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.readiness import HTTP_STATUS, readiness_snapshot

router = APIRouter(tags=["ops"])

SERVICE_NAME = "jobsearchagent-v2"
SERVICE_VERSION = "2.0.0"


@router.get("/health")
def health() -> dict:
    """Liveness: the process is up and serving. No dependency checks."""
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


@router.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness: shared-dependency probe. 200 ready/degraded, 503 down."""
    snapshot = readiness_snapshot()
    return JSONResponse(
        status_code=HTTP_STATUS.get(snapshot["status"], 200),
        content=snapshot,
    )
