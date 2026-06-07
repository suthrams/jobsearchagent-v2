"""Readiness checks for the /readyz endpoint (ADR-084).

Probes the SHARED dependencies every endpoint relies on - the database plus the
provider/scraper credentials - and aggregates a ready / degraded / down verdict.
We deliberately do NOT probe the 30 individual routes (most are mutating; see
ADR-084); a healthy shared substrate is the meaningful "is the plumbing up" signal.

Design properties:
- Pure + injectable (`db_path`, `getenv`) so every branch is unit-testable without
  touching the real environment or DB.
- Never raises: a failing check yields `{"ok": False, "detail": ...}`, not an
  exception. `/readyz` returning 503 is a correct signal, not a crash.
- Secret-safe: reports presence / mode only (`live`/`mock`, `configured`/`not
  configured`) - never the value of any API key, never PII.

See docs/architecture/health_check_design.md.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.repositories.database import DEFAULT_DB_PATH

# status -> HTTP code. Only a down database (the one critical dependency) yields 503;
# a missing capability (mock mode, no Adzuna) is degraded but still served (200).
HTTP_STATUS: dict[str, int] = {"ready": 200, "degraded": 200, "down": 503}


def _check_database(db_path: Path) -> dict:
    """Critical: open the SQLite DB and run SELECT 1."""
    start = time.perf_counter()
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"ok": True, "detail": "SELECT 1 ok",
                "latency_ms": int((time.perf_counter() - start) * 1000)}
    except Exception as e:  # noqa: BLE001 - readiness never raises
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def _check_agent_provider(getenv: Callable[[str], str | None]) -> dict:
    """Capability: ANTHROPIC_API_KEY present -> live agents; absent -> mock mode.

    Mock mode is a valid run mode (UI + reads work), so absence is degraded, not
    down. Presence/absence only - never the key value.
    """
    live = bool(getenv("ANTHROPIC_API_KEY"))
    return {"ok": live, "mode": "live" if live else "mock",
            "detail": "live" if live else "mock"}


def _check_adzuna(getenv: Callable[[str], str | None]) -> dict:
    """Capability: both Adzuna credentials present -> discovery available."""
    ok = bool(getenv("ADZUNA_APP_ID") and getenv("ADZUNA_APP_KEY"))
    return {"ok": ok, "detail": "configured" if ok else "not configured"}


def _check_openai(getenv: Callable[[str], str | None]) -> dict:
    """Optional: alternate provider. Reported, but never affects status."""
    ok = bool(getenv("OPENAI_API_KEY"))
    return {"ok": ok, "detail": "configured" if ok else "not configured",
            "optional": True}


# Capability checks gate ready-vs-degraded (never down). `openai` is optional and
# excluded here on purpose.
_CAPABILITY_CHECKS = ("agent_provider", "adzuna")


def readiness_snapshot(
    db_path: Path = DEFAULT_DB_PATH,
    getenv: Callable[[str], str | None] = os.getenv,
) -> dict:
    """Run the check registry and aggregate.

    Returns {"status": "ready"|"degraded"|"down", "checks": {...}, "checked_at"}.
    """
    checks = {
        "database": _check_database(db_path),
        "agent_provider": _check_agent_provider(getenv),
        "adzuna": _check_adzuna(getenv),
        "openai": _check_openai(getenv),
    }
    if not checks["database"]["ok"]:
        status = "down"
    elif all(checks[name]["ok"] for name in _CAPABILITY_CHECKS):
        status = "ready"
    else:
        status = "degraded"
    return {
        "status": status,
        "checks": checks,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
