"""Tests for ModelRegistry — ADR-053."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.claude_provider import ClaudeProvider
from app.providers.model_registry import (
    DEFAULT_AGENT_ASSIGNMENT,
    KNOWN_MODELS,
    ModelRegistry,
    UnknownModelError,
    assignment_from_config,
)
from app.providers.openai_provider import OpenAIProvider
from app.providers.prompt_loader import PromptLoader


def _loader(tmp_path: Path) -> PromptLoader:
    shared = tmp_path / "shared"
    agents = tmp_path / "agents"
    shared.mkdir()
    agents.mkdir()
    (shared / "guardrails.txt").write_text("guardrails", encoding="utf-8")
    (agents / "research_agent.txt").write_text("# version: 1\nrole", encoding="utf-8")
    return PromptLoader(tmp_path)


def test_default_assignment_resolves_known_models():
    """Every default assignment must reference a model in KNOWN_MODELS."""
    for agent, a in DEFAULT_AGENT_ASSIGNMENT.items():
        assert a["provider"] in KNOWN_MODELS, f"{agent}: bad provider"
        assert a["model"] in KNOWN_MODELS[a["provider"]], f"{agent}: bad model"


def test_build_uses_defaults_when_no_overrides(tmp_path):
    reg = ModelRegistry.build(_loader(tmp_path), agent_assignment={}, openai_available=False)
    assignment = reg.assignment()
    for agent, default in DEFAULT_AGENT_ASSIGNMENT.items():
        assert assignment[agent] == default


def test_build_applies_user_overrides(tmp_path):
    overrides = {
        "research_agent": {"provider": "claude", "model": "claude-sonnet-4-6"},
    }
    reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)
    assert reg.assignment()["research_agent"] == overrides["research_agent"]


def test_build_falls_back_when_openai_unavailable(tmp_path, caplog):
    """A user override pointing to openai but OPENAI_API_KEY missing → keep default."""
    overrides = {"research_agent": {"provider": "openai", "model": "gpt-4o-mini"}}
    reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)
    # Should have kept the default Claude assignment
    assert reg.assignment()["research_agent"]["provider"] == "claude"


def test_build_rejects_unknown_provider(tmp_path):
    overrides = {"research_agent": {"provider": "bogus", "model": "claude-sonnet-4-6"}}
    with pytest.raises(UnknownModelError):
        ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)


def test_build_rejects_unknown_model(tmp_path):
    overrides = {"research_agent": {"provider": "claude", "model": "claude-future-model"}}
    with pytest.raises(UnknownModelError):
        ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)


def test_for_agent_returns_correct_provider_class(tmp_path):
    reg = ModelRegistry.build(_loader(tmp_path), agent_assignment={}, openai_available=True)
    assert isinstance(reg.for_agent("research_agent"), ClaudeProvider)


def test_for_agent_returns_openai_when_assigned(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    overrides = {"research_agent": {"provider": "openai", "model": "gpt-4o-mini"}}
    reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=True)
    assert isinstance(reg.for_agent("research_agent"), OpenAIProvider)


def test_provider_instances_shared_across_same_model(tmp_path):
    """Two agents with the same (provider, model) get the same instance — no duplication."""
    overrides = {
        "research_agent": {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        "scoring_agent":  {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    }
    reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)
    assert reg.for_agent("research_agent") is reg.for_agent("scoring_agent")


def test_catalog_marks_openai_unavailable_when_no_key():
    cat = ModelRegistry.catalog(openai_available=False)
    assert cat["openai"]["available"] is False
    assert cat["claude"]["available"] is True
    assert all("input_per_m" in m for m in cat["claude"]["models"])


def test_assignment_from_config_extracts_agents_block():
    eff = {
        "agents": {
            "research_agent": {"provider": "openai", "model": "gpt-4o-mini"},
            "scoring_agent":  {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
            "broken_entry":   "not a dict",  # should be ignored
        },
    }
    out = assignment_from_config(eff)
    assert out["research_agent"]["provider"] == "openai"
    assert out["scoring_agent"]["model"] == "claude-haiku-4-5-20251001"
    assert "broken_entry" not in out


def test_assignment_from_config_empty_when_no_agents_block():
    assert assignment_from_config({}) == {}
    assert assignment_from_config({"search": {}}) == {}
