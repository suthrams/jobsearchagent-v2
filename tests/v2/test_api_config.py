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
