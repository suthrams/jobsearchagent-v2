"""ADR-098: per-profile ATS targeting, managed in the Settings UI.

Covers the runtime fix (factory resolves the company list per run from the
profile's effective_config, not deps-time system config), the deep-merge replace
semantics, and the verify-on-add endpoint. httpx is patched so no test hits the
network.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.services import ats_scrapers
from app.services.ats_scrapers import build_ats_scrapers, verify_ats_board
from app.services.config_service import ConfigService
from app.services.job_discovery_service import JobDiscoveryService
from app.workflows.nodes.discover_jobs import make_discover_jobs_node


# ── verify_ats_board: the single live-check both the endpoint and the tool use ──

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_verify_ats_board_healthy_greenhouse(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, {"jobs": [{}, {}, {}]}))
    assert verify_ats_board("greenhouse", "stripe") == 3


def test_verify_ats_board_healthy_lever(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, [{}, {}]))
    assert verify_ats_board("lever", "gopuff") == 2


def test_verify_ats_board_dead_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(404, None))
    assert verify_ats_board("greenhouse", "does-not-exist") is None


def test_verify_ats_board_unknown_ats_and_blank_slug(monkeypatch):
    # No network call should be needed for these early rejections.
    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("verify_ats_board should not hit the network here")
    monkeypatch.setattr(httpx, "get", _boom)
    assert verify_ats_board("workday", "acme") is None
    assert verify_ats_board("greenhouse", "  ") is None


def test_verify_ats_board_network_error_is_none(monkeypatch):
    def _raise(*a, **k):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", "https://x"))
    monkeypatch.setattr(httpx, "get", _raise)
    assert verify_ats_board("lever", "gopuff") is None


# ── runtime fix: the company list is resolved PER RUN from effective_config ─────

def test_build_ats_scrapers_is_per_run_replace():
    """Two different per-run scrapers configs yield two different board sets -
    proving the list is a per-run input, not a deps-time global."""
    cfg_a = {"greenhouse": {"companies": ["alpha"]}}
    cfg_b = {"greenhouse": {"companies": ["beta", "gamma"]}}
    a = build_ats_scrapers(["Engineer"], cfg_a)
    b = build_ats_scrapers(["Engineer"], cfg_b)
    assert a and b
    assert a[0]._tokens == ["alpha"]
    assert b[0]._tokens == ["beta", "gamma"]


def test_discover_jobs_passes_effective_config_scrapers_to_factory():
    """The node reads scrapers from THIS run's effective_config and hands it to the
    ATS factory (the ADR-098 seam)."""
    svc = MagicMock(spec=JobDiscoveryService)
    svc.discover_with_stats.return_value = ([], {})

    captured: dict = {}

    def fake_factory(roles, scrapers_cfg):
        captured["roles"] = roles
        captured["scrapers_cfg"] = scrapers_cfg
        return []

    node = make_discover_jobs_node(
        svc, MagicMock(), MagicMock(), ats_scraper_factory=fake_factory,
    )
    state = {
        "workflow_id": "wf-1",
        "search_criteria": {"roles": ["Staff Engineer"]},
        "errors": [],
        "effective_config": {
            "scrapers": {"greenhouse": {"companies": ["my-company"]}},
        },
    }
    node(state)
    assert captured["roles"] == ["Staff Engineer"]
    assert captured["scrapers_cfg"] == {"greenhouse": {"companies": ["my-company"]}}


def test_discover_jobs_factory_failure_is_non_fatal():
    """A factory blow-up must never lose the run (never-lose-the-run)."""
    svc = MagicMock(spec=JobDiscoveryService)
    svc.discover_with_stats.return_value = ([], {})

    def boom_factory(roles, scrapers_cfg):
        raise RuntimeError("factory exploded")

    node = make_discover_jobs_node(
        svc, MagicMock(), MagicMock(), ats_scraper_factory=boom_factory,
    )
    result = node({"workflow_id": "wf-1", "search_criteria": {"roles": ["X"]},
                   "errors": [], "effective_config": {}})
    assert result["normalized_jobs"] == []


# ── deep-merge replace semantics: a profile override replaces the default list ──

def test_effective_config_override_replaces_default_list(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "scrapers:\n"
        "  greenhouse:\n"
        "    enabled: true\n"
        "    companies: [aaa, bbb, ccc]\n",
        encoding="utf-8",
    )
    db = tmp_path / "v2.db"
    from app.repositories.database import init_db
    init_db(db)
    svc = ConfigService(config_path=cfg, db_path=db)

    # No override -> a new profile inherits the shared default (decision 2).
    base = svc.get_effective_config("1")
    assert base["scrapers"]["greenhouse"]["companies"] == ["aaa", "bbb", "ccc"]

    # Profile-level override REPLACES the list (decision 3), not appends.
    from app.repositories.config_repository import ConfigRepository
    ConfigRepository(db).upsert(
        "user_1__scrapers.greenhouse.companies", "1",
        "scrapers.greenhouse.companies", ["zzz"],
    )
    eff = svc.get_effective_config("1")
    assert eff["scrapers"]["greenhouse"]["companies"] == ["zzz"]
    # The other profile still sees the default (isolation).
    assert svc.get_effective_config("2")["scrapers"]["greenhouse"]["companies"] == \
        ["aaa", "bbb", "ccc"]


# ── verify-on-add endpoint ─────────────────────────────────────────────────────

def test_verify_endpoint_accepts_live_slug(monkeypatch):
    monkeypatch.setattr(ats_scrapers, "verify_ats_board", lambda ats, slug: 7)
    client = TestClient(app)
    r = client.post("/config/ats/verify", json={"ats": "greenhouse", "slug": "stripe"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["job_count"] == 7


def test_verify_endpoint_rejects_dead_slug(monkeypatch):
    monkeypatch.setattr(ats_scrapers, "verify_ats_board", lambda ats, slug: None)
    client = TestClient(app)
    r = client.post("/config/ats/verify", json={"ats": "lever", "slug": "nope"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["job_count"] == 0


def test_verify_endpoint_rejects_unknown_ats():
    client = TestClient(app)
    r = client.post("/config/ats/verify", json={"ats": "workday", "slug": "acme"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "unknown_ats"


def test_verify_endpoint_rejects_empty_slug():
    client = TestClient(app)
    r = client.post("/config/ats/verify", json={"ats": "greenhouse", "slug": "  "})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "empty_slug"
