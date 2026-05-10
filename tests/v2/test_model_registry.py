"""Tests for ModelRegistry — ADR-053."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.claude_provider import ClaudeProvider
from app.providers.model_registry import (
    DEFAULT_AGENT_ASSIGNMENT,
    HIGH_VOLUME_AGENTS,
    HIGH_VOLUME_SAFE_MODELS,
    KNOWN_MODELS,
    ModelRegistry,
    UnknownModelError,
    assignment_from_config,
    is_cost_capped_agent,
    is_high_volume_safe_model,
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
    """Overrides are honored for agents that aren't cost-capped."""
    overrides = {
        "career_advisor": {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
    }
    reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)
    assert reg.assignment()["career_advisor"] == overrides["career_advisor"]


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


# ── Cost-cap guardrail (high-volume agents pinned to cheap allowlist) ────────

def test_high_volume_agents_are_research_and_scoring():
    """Sanity: the cap covers exactly the per-job, multi-call agents."""
    assert HIGH_VOLUME_AGENTS == frozenset({"research_agent", "scoring_agent"})
    # Defaults must be safe.
    for agent in HIGH_VOLUME_AGENTS:
        assert DEFAULT_AGENT_ASSIGNMENT[agent]["model"] in HIGH_VOLUME_SAFE_MODELS


def test_high_volume_safe_models_includes_haiku_and_gpt4o_mini():
    """Allowlist tracks the cheapest models registered in either provider."""
    assert "claude-haiku-4-5-20251001" in HIGH_VOLUME_SAFE_MODELS
    assert "gpt-4o-mini" in HIGH_VOLUME_SAFE_MODELS
    # Sonnet, Opus, gpt-4o, o1 are NOT high-volume safe.
    assert "claude-sonnet-4-6" not in HIGH_VOLUME_SAFE_MODELS
    assert "claude-opus-4-7" not in HIGH_VOLUME_SAFE_MODELS
    assert "gpt-4o" not in HIGH_VOLUME_SAFE_MODELS


def test_helpers_match_constants():
    assert is_cost_capped_agent("scoring_agent") is True
    assert is_cost_capped_agent("research_agent") is True
    assert is_cost_capped_agent("career_advisor") is False
    assert is_high_volume_safe_model("claude-haiku-4-5-20251001") is True
    assert is_high_volume_safe_model("claude-sonnet-4-6") is False


def test_build_snaps_high_volume_agent_back_when_assigned_sonnet(tmp_path, caplog):
    """A user override pinning scoring_agent to Sonnet must be reverted to the
    default Haiku, with a warning. This is the durable line of defense — if the
    user bypasses the API/UI and writes directly to user_config, the registry
    refuses to honor the assignment at next build."""
    import logging
    overrides = {
        "scoring_agent": {"provider": "claude", "model": "claude-sonnet-4-6"},
    }
    with caplog.at_level(logging.WARNING, logger="app.providers.model_registry"):
        reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)
    # Snapped back to default
    assert reg.assignment()["scoring_agent"] == DEFAULT_AGENT_ASSIGNMENT["scoring_agent"]
    # Warning logged with the agent name
    assert "scoring_agent" in caplog.text
    assert "high-volume" in caplog.text


def test_build_keeps_high_volume_agent_when_assigned_safe_alternative(tmp_path, monkeypatch):
    """gpt-4o-mini is in the allowlist (cheaper than Haiku) — the override sticks."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    overrides = {
        "research_agent": {"provider": "openai", "model": "gpt-4o-mini"},
    }
    reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=True)
    assert reg.assignment()["research_agent"]["model"] == "gpt-4o-mini"


def test_build_does_not_constrain_low_volume_agents(tmp_path):
    """career_advisor and friends remain freely configurable — the cap is
    targeted at the multi-call-per-job agents only."""
    overrides = {
        "career_advisor": {"provider": "claude", "model": "claude-opus-4-7"},
    }
    reg = ModelRegistry.build(_loader(tmp_path), overrides, openai_available=False)
    assert reg.assignment()["career_advisor"]["model"] == "claude-opus-4-7"


def test_catalog_includes_cost_cap_metadata():
    """UI needs the cap metadata to filter dropdowns without hard-coding constants."""
    cat = ModelRegistry.catalog(openai_available=False)
    meta = cat.get("_meta") or {}
    assert set(meta.get("high_volume_agents") or []) == set(HIGH_VOLUME_AGENTS)
    assert set(meta.get("high_volume_safe_models") or []) == set(HIGH_VOLUME_SAFE_MODELS)
