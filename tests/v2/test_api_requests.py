"""API-request observability tests (ADR-074 Gap 5).

The HTTP layer was unobserved (CORS was the only middleware). Gap 5 adds a
middleware that records every request into api_requests by route TEMPLATE
(PII-safe). Guards:

  1. Forcing-function — the http middleware is registered (so the layer can't
     silently go dark).
  2. Middleware behavior — a request with an id in the path records the route
     TEMPLATE, not the raw id (PII-safe), plus method/status/user_id.
  3. Read — ApiRequestRepository scoping + api_summary aggregation.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.main as main_module
from app.api.dependencies import get_deps
from app.api.main import app
from app.repositories.api_request_repository import ApiRequestRepository
from app.repositories.database import init_db
from app.services import system_health as sh

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


# ── Layer 1: forcing function ─────────────────────────────────────────────────


def test_http_middleware_is_registered():
    src = (APP_DIR / "api" / "main.py").read_text(encoding="utf-8")
    assert '@app.middleware("http")' in src, "the request-observability middleware is gone"
    assert "record_api_request_safe(" in src


# ── Layer 2: middleware behavior (PII-safe route template) ────────────────────


def test_middleware_records_route_template_not_raw_id(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        main_module, "record_api_request_safe",
        lambda **kw: captured.append(kw),
    )
    # Mock deps so GET /tailorings/{id} returns a clean 404 (repo returns None).
    mock_deps = MagicMock()
    mock_deps.tailoring_repo.get_by_id.return_value = None
    app.dependency_overrides[get_deps] = lambda: mock_deps
    try:
        client = TestClient(app)  # no lifespan -> no graph build
        resp = client.get("/tailorings/secret-resume-id-123?user_id=2")
    finally:
        app.dependency_overrides.pop(get_deps, None)

    assert resp.status_code == 404
    assert len(captured) == 1
    ev = captured[0]
    assert ev["method"] == "GET"
    assert ev["route_template"] == "/tailorings/{tailoring_id}"
    assert "secret-resume-id-123" not in ev["route_template"]  # PII-safe
    assert ev["status_code"] == 404
    assert ev["user_id"] == "2"


# ── Layer 3: read layer ───────────────────────────────────────────────────────


def _seed(db_path):
    r = ApiRequestRepository(db_path)
    r.create("a1", "1", "POST", "/workflows", 202, 120)
    r.create("a2", "1", "GET", "/config", 200, 30)
    r.create("a3", "1", "POST", "/tailorings/{tailoring_id}/decisions", 502, 900)
    r.create("a4", "0", "GET", "/config", 200, 25)


def test_list_for_user_scopes_by_profile(db_path):
    _seed(db_path)
    r = ApiRequestRepository(db_path)
    assert len(r.list_for_user("1")) == 3
    assert len(r.list_for_user("0")) == 1
    assert len(r.list_for_user()) == 4


def test_api_summary_counts_errors_and_latency(db_path):
    _seed(db_path)
    summ = sh.api_summary(user_id="1", db_path=db_path)
    assert summ["total"] == 3
    assert summ["error_count"] == 1          # the 502
    assert 0.33 < summ["error_rate"] < 0.34  # 1/3
    routes = {(d["method"], d["route_template"]) for d in summ["by_endpoint"]}
    assert ("POST", "/tailorings/{tailoring_id}/decisions") in routes
