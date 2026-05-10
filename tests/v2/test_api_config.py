"""Tests for the /config router — read effective config and write user overrides."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.main import app
from app.repositories.database import init_db
from app.services import config_service as config_service_module


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point ConfigService at a temp YAML + temp DB so tests don't touch real config."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "search": {"titles": ["Engineer"], "locations": ["Remote"], "max_jobs": 10},
        "scoring": {"min_match_score": 75},
    }), encoding="utf-8")

    db_path = tmp_path / "v2.db"
    init_db(db_path)

    monkeypatch.setattr(config_service_module, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr("app.api.routers.config.DEFAULT_DB_PATH", db_path)

    # Patch ConfigService default db_path arg
    original_init = config_service_module.ConfigService.__init__

    def patched_init(self, config_path=cfg_path, db_path=db_path):
        original_init(self, config_path=config_path, db_path=db_path)

    monkeypatch.setattr(config_service_module.ConfigService, "__init__", patched_init)
    return cfg_path, db_path


def test_get_config_returns_effective_and_protected_keys(isolated_config):
    client = TestClient(app)
    response = client.get("/config")
    assert response.status_code == 200
    body = response.json()
    assert "effective_config" in body
    assert "protected_keys" in body
    assert isinstance(body["protected_keys"], list)
    assert "llm.default_model" in body["protected_keys"]
    assert body["effective_config"]["scoring"]["min_match_score"] == 75


def test_put_config_persists_override(isolated_config):
    client = TestClient(app)
    response = client.put("/config", json={"key": "scoring.min_match_score", "value": 65})
    assert response.status_code == 200
    assert response.json()["status"] == "saved"

    follow_up = client.get("/config").json()
    assert follow_up["effective_config"]["scoring"]["min_match_score"] == 65


def test_put_config_rejects_protected_key(isolated_config):
    client = TestClient(app)
    response = client.put("/config", json={"key": "llm.default_model", "value": "claude-haiku-4-5"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "protected_key"


# ── ADR-053: per-agent assignment endpoints ──────────────────────────────────

def test_get_providers_returns_catalog_and_assignment(isolated_config, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    response = client.get("/config/providers")
    assert response.status_code == 200
    body = response.json()
    assert "providers" in body
    assert "agent_assignment" in body
    assert "claude" in body["providers"]
    assert body["providers"]["claude"]["available"] is True
    assert body["providers"]["openai"]["available"] is False
    # Defaults are present even with no overrides
    assert "research_agent" in body["agent_assignment"]


def test_put_agent_provider_accepts_known_value(isolated_config, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    r = client.put("/config", json={"key": "agents.research_agent.provider", "value": "claude"})
    assert r.status_code == 200


def test_put_agent_model_rejects_unknown_value(isolated_config):
    client = TestClient(app)
    r = client.put("/config", json={"key": "agents.research_agent.model", "value": "claude-future-x"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "unknown_model"


def test_put_unknown_agent_rejected(isolated_config):
    client = TestClient(app)
    r = client.put("/config", json={"key": "agents.bogus_agent.model", "value": "claude-sonnet-4-6"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "unknown_agent"


def test_put_full_agent_assignment_validated(isolated_config):
    client = TestClient(app)
    r = client.put("/config", json={
        "key": "agents.research_agent",
        "value": {"provider": "openai", "model": "gpt-4o-mini"},
    })
    assert r.status_code == 200

    r = client.put("/config", json={
        "key": "agents.research_agent",
        "value": {"provider": "openai"},  # missing model
    })
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "incomplete_assignment"


# ── Cost-cap enforcement on PUT /config ──────────────────────────────────────

def test_put_agent_model_rejects_sonnet_for_scoring(isolated_config):
    """Cost guardrail: scoring_agent is high-volume, Sonnet is not in the
    allowlist. A direct API write must fail loudly with cost_cap_violation
    rather than silently snap back at next reload."""
    client = TestClient(app)
    r = client.put("/config", json={
        "key": "agents.scoring_agent.model",
        "value": "claude-sonnet-4-6",
    })
    assert r.status_code == 422
    body = r.json()
    assert body["detail"]["error"] == "cost_cap_violation"
    assert "scoring_agent" in body["detail"]["message"]


def test_put_agent_model_rejects_sonnet_for_research(isolated_config):
    client = TestClient(app)
    r = client.put("/config", json={
        "key": "agents.research_agent.model",
        "value": "claude-opus-4-7",
    })
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "cost_cap_violation"


def test_put_agent_model_accepts_haiku_for_scoring(isolated_config):
    """Haiku 4.5 is in the cost-cap allowlist, so the assignment goes through."""
    client = TestClient(app)
    r = client.put("/config", json={
        "key": "agents.scoring_agent.model",
        "value": "claude-haiku-4-5-20251001",
    })
    assert r.status_code == 200


def test_put_agent_model_accepts_gpt4o_mini_for_scoring(isolated_config):
    """gpt-4o-mini is also in the allowlist (cheaper than Haiku) — accepted."""
    client = TestClient(app)
    r = client.put("/config", json={
        "key": "agents.scoring_agent.model",
        "value": "gpt-4o-mini",
    })
    assert r.status_code == 200


def test_put_full_assignment_rejects_sonnet_for_high_volume_agent(isolated_config):
    """The combined-write form (PUT agents.{name} with full {provider, model})
    must apply the cap as well — otherwise the per-key form is the only
    enforcement and the combined form is a bypass."""
    client = TestClient(app)
    r = client.put("/config", json={
        "key": "agents.scoring_agent",
        "value": {"provider": "claude", "model": "claude-sonnet-4-6"},
    })
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "cost_cap_violation"


def test_put_agent_model_does_not_cap_low_volume_agents(isolated_config):
    """career_advisor is not high-volume; it can still be assigned Sonnet."""
    client = TestClient(app)
    r = client.put("/config", json={
        "key": "agents.career_advisor.model",
        "value": "claude-sonnet-4-6",
    })
    assert r.status_code == 200


def test_get_providers_includes_cost_cap_metadata(isolated_config, monkeypatch):
    """UI consumes _meta to filter dropdowns; absence here would mean the
    Settings page silently shows expensive options for capped agents."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    r = client.get("/config/providers")
    assert r.status_code == 200
    body = r.json()
    meta = body["providers"].get("_meta") or {}
    assert "scoring_agent" in (meta.get("high_volume_agents") or [])
    assert "research_agent" in (meta.get("high_volume_agents") or [])
    assert "claude-haiku-4-5-20251001" in (meta.get("high_volume_safe_models") or [])
    assert "gpt-4o-mini" in (meta.get("high_volume_safe_models") or [])


# ── ADR-053 addendum: POST /config/reload ────────────────────────────────────
# Reload supersedes restart for runtime-overridable settings. These tests pin
# the contract: endpoint returns the live agent assignment after rebuild.

def test_reload_endpoint_returns_status_and_assignment(isolated_config, monkeypatch):
    """Smoke test the reload endpoint. Mocks reload_deps_and_graph so we don't
    actually rebuild a real graph (no API key, no real provider clients)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake_assignment = {
        "research_agent":  {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        "scoring_agent":   {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    }
    monkeypatch.setattr(
        "app.api.dependencies.reload_deps_and_graph",
        lambda: {"agent_assignment": fake_assignment},
    )

    client = TestClient(app)
    r = client.post("/config/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reloaded"
    assert body["agent_assignment"] == fake_assignment


def test_reload_endpoint_surfaces_rebuild_failure_as_500(isolated_config, monkeypatch):
    """If reload_deps_and_graph raises, the endpoint must surface a 500 with
    the standard error envelope (not a stack trace leak)."""
    def _boom():
        raise RuntimeError("provider client failed to start")
    monkeypatch.setattr("app.api.dependencies.reload_deps_and_graph", _boom)

    client = TestClient(app)
    r = client.post("/config/reload")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"]["error"] == "reload_failed"
    assert "provider client failed to start" in body["detail"]["message"]


def test_reload_endpoint_idempotent(isolated_config, monkeypatch):
    """Calling reload twice in a row must not break — each call rebuilds
    cleanly. Counts the rebuild invocations to make sure both fire."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    call_count = {"n": 0}
    def _track():
        call_count["n"] += 1
        return {"agent_assignment": {}}
    monkeypatch.setattr("app.api.dependencies.reload_deps_and_graph", _track)

    client = TestClient(app)
    assert client.post("/config/reload").status_code == 200
    assert client.post("/config/reload").status_code == 200
    assert call_count["n"] == 2
