"""Liveness + readiness tests (ADR-084).

Covers:
  1. The readiness service - each shared-dependency check, the ready/degraded/down
     aggregation truth table, and secret-safety (no key value ever in the output).
  2. The endpoints - GET /health (200), GET /readyz status->code mapping.
  3. The ADR-074 interaction - /health and /readyz are excluded from api_requests.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

import app.api.main as main_module
import app.api.routers.health as health_module
from app.api.main import app
from app.services import readiness


def _getenv(present: set[str], value: str = "x"):
    """Fake os.getenv: returns `value` for keys in `present`, else None."""
    return lambda k: value if k in present else None


# ── 1. readiness service: individual checks ───────────────────────────────────


def test_database_check_ok(tmp_path):
    res = readiness._check_database(tmp_path / "ok.db")
    assert res["ok"] is True
    assert "latency_ms" in res


def test_database_check_down(tmp_path):
    # Parent directory does not exist -> sqlite cannot open the file.
    res = readiness._check_database(tmp_path / "nope" / "x.db")
    assert res["ok"] is False
    assert "ok" not in res["detail"].lower() or "error" in res["detail"].lower()


def test_agent_provider_live_and_mock():
    assert readiness._check_agent_provider(_getenv({"ANTHROPIC_API_KEY"})) == {
        "ok": True, "mode": "live", "detail": "live"}
    assert readiness._check_agent_provider(_getenv(set())) == {
        "ok": False, "mode": "mock", "detail": "mock"}


def test_adzuna_check_needs_both_keys():
    assert readiness._check_adzuna(_getenv({"ADZUNA_APP_ID", "ADZUNA_APP_KEY"}))["ok"] is True
    assert readiness._check_adzuna(_getenv({"ADZUNA_APP_ID"}))["ok"] is False  # only one
    assert readiness._check_adzuna(_getenv(set()))["ok"] is False


def test_openai_check_is_optional():
    res = readiness._check_openai(_getenv(set()))
    assert res["ok"] is False and res["optional"] is True


# ── 1b. readiness service: aggregation truth table ────────────────────────────


def test_snapshot_ready(tmp_path):
    snap = readiness.readiness_snapshot(
        db_path=tmp_path / "v2.db",
        getenv=_getenv({"ANTHROPIC_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "OPENAI_API_KEY"}),
    )
    assert snap["status"] == "ready"
    assert readiness.HTTP_STATUS[snap["status"]] == 200


def test_snapshot_degraded_in_mock_mode(tmp_path):
    snap = readiness.readiness_snapshot(
        db_path=tmp_path / "v2.db",
        getenv=_getenv({"ADZUNA_APP_ID", "ADZUNA_APP_KEY"}),  # no ANTHROPIC -> mock
    )
    assert snap["status"] == "degraded"
    assert readiness.HTTP_STATUS[snap["status"]] == 200


def test_snapshot_degraded_without_adzuna(tmp_path):
    snap = readiness.readiness_snapshot(
        db_path=tmp_path / "v2.db",
        getenv=_getenv({"ANTHROPIC_API_KEY"}),  # live agents, but no discovery creds
    )
    assert snap["status"] == "degraded"


def test_snapshot_down_when_db_unreachable(tmp_path):
    snap = readiness.readiness_snapshot(
        db_path=tmp_path / "missing" / "v2.db",
        getenv=_getenv({"ANTHROPIC_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY"}),
    )
    assert snap["status"] == "down"
    assert readiness.HTTP_STATUS[snap["status"]] == 503


def test_snapshot_is_secret_safe(tmp_path):
    # Keys resolve to a recognisable secret value; it must never appear in output.
    snap = readiness.readiness_snapshot(
        db_path=tmp_path / "v2.db",
        getenv=_getenv(
            {"ANTHROPIC_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "OPENAI_API_KEY"},
            value="sk-SUPER-SECRET-VALUE",
        ),
    )
    assert "SUPER-SECRET" not in json.dumps(snap)


# ── 2. endpoints ──────────────────────────────────────────────────────────────


def test_health_endpoint_is_alive():
    resp = TestClient(app).get("/health")  # plain client -> no lifespan/graph build
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "jobsearchagent-v2"


def test_readyz_maps_ready_to_200(monkeypatch):
    monkeypatch.setattr(health_module, "readiness_snapshot",
                        lambda: {"status": "ready", "checks": {}, "checked_at": "t"})
    resp = TestClient(app).get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_readyz_maps_down_to_503(monkeypatch):
    monkeypatch.setattr(health_module, "readiness_snapshot",
                        lambda: {"status": "down", "checks": {}, "checked_at": "t"})
    resp = TestClient(app).get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "down"


# ── 3. ADR-074 interaction: health probes are NOT recorded ────────────────────


def test_health_endpoints_excluded_from_api_requests(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(main_module, "record_api_request_safe",
                        lambda **kw: captured.append(kw))
    client = TestClient(app)
    client.get("/health")
    client.get("/readyz")
    recorded = {c["route_template"] for c in captured}
    assert "/health" not in recorded
    assert "/readyz" not in recorded
