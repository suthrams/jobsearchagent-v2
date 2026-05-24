"""Tests for ModelRegistry — ADR-053 + ADR-058 (catalog and defaults in YAML)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.claude_provider import ClaudeProvider
from app.providers.model_registry import (
    HIGH_VOLUME_AGENTS,
    HIGH_VOLUME_SAFE_MODELS,
    ModelCatalogEntry,
    ModelConfigError,
    ModelRegistry,
    UnknownModelError,
    assignment_from_config,
    catalog_from_config,
    defaults_from_config,
    is_cost_capped_agent,
    is_high_volume_safe_model,
    known_models_from_catalog,
)
from app.providers.openai_provider import OpenAIProvider
from app.providers.prompt_loader import PromptLoader


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _loader(tmp_path: Path) -> PromptLoader:
    shared = tmp_path / "shared"
    agents = tmp_path / "agents"
    shared.mkdir()
    agents.mkdir()
    (shared / "guardrails.txt").write_text("guardrails", encoding="utf-8")
    (agents / "research_agent.txt").write_text("# version: 1\nrole", encoding="utf-8")
    return PromptLoader(tmp_path)


def _catalog() -> dict[str, list[ModelCatalogEntry]]:
    """The canonical test catalog used across these tests."""
    return {
        "claude": [
            ModelCatalogEntry("claude", "claude-haiku-4-5-20251001", 1.00, 5.00),
            ModelCatalogEntry("claude", "claude-sonnet-4-6", 3.00, 15.00),
            ModelCatalogEntry("claude", "claude-opus-4-7", 15.00, 75.00),
        ],
        "openai": [
            ModelCatalogEntry("openai", "gpt-4o-mini", 0.15, 0.60),
            ModelCatalogEntry("openai", "gpt-4o", 2.50, 10.00),
            ModelCatalogEntry("openai", "o1", 15.00, 60.00),
        ],
    }


def _default_assignment() -> dict[str, dict[str, str]]:
    """A representative assignment mirroring config.example.yaml."""
    return {
        "research_agent":    {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        "scoring_agent":     {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        "resume_critic":     {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        "review_auditor":    {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        "fidelity_reviewer": {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        "career_advisor":    {"provider": "claude", "model": "claude-sonnet-4-6"},
        "interview_coach":   {"provider": "claude", "model": "claude-sonnet-4-6"},
        "tailoring_agent":   {"provider": "claude", "model": "claude-sonnet-4-6"},
    }


# ── Config parsing ───────────────────────────────────────────────────────────

def test_catalog_from_config_parses_valid_yaml_shape():
    eff = {
        "models": {
            "providers": {
                "claude": [
                    {"id": "claude-haiku-4-5-20251001", "input_per_m": 1.0, "output_per_m": 5.0},
                ],
                "openai": [
                    {"id": "gpt-4o-mini", "input_per_m": 0.15, "output_per_m": 0.60},
                ],
            }
        }
    }
    cat = catalog_from_config(eff)
    assert len(cat["claude"]) == 1
    assert cat["claude"][0].model == "claude-haiku-4-5-20251001"
    assert cat["openai"][0].input_per_m == 0.15


def test_catalog_from_config_raises_when_block_missing():
    with pytest.raises(ModelConfigError):
        catalog_from_config({})


def test_catalog_from_config_raises_on_malformed_entry():
    eff = {
        "models": {
            "providers": {
                "claude": [{"id": "m1"}],  # missing pricing fields
            }
        }
    }
    with pytest.raises(ModelConfigError):
        catalog_from_config(eff)


def test_defaults_from_config_parses_agents_block():
    eff = {
        "agents": {
            "research_agent": {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
        }
    }
    out = defaults_from_config(eff)
    assert out["research_agent"]["provider"] == "claude"


def test_defaults_from_config_raises_when_block_missing():
    with pytest.raises(ModelConfigError):
        defaults_from_config({})


def test_assignment_from_config_extracts_agents_block_lenient():
    """assignment_from_config (used for overrides) is permissive; missing block returns {}."""
    eff = {
        "agents": {
            "research_agent": {"provider": "openai", "model": "gpt-4o-mini"},
            "broken_entry":   "not a dict",  # should be ignored
        },
    }
    out = assignment_from_config(eff)
    assert out["research_agent"]["provider"] == "openai"
    assert "broken_entry" not in out


def test_assignment_from_config_empty_when_no_agents_block():
    assert assignment_from_config({}) == {}
    assert assignment_from_config({"search": {}}) == {}


def test_known_models_adapter_returns_legacy_shape():
    cat = _catalog()
    km = known_models_from_catalog(cat)
    assert "claude-haiku-4-5-20251001" in km["claude"]
    assert "gpt-4o-mini" in km["openai"]


# ── Registry build ───────────────────────────────────────────────────────────

def test_build_uses_defaults_when_no_overrides(tmp_path):
    reg = ModelRegistry.build(
        _loader(tmp_path), _catalog(), _default_assignment(), openai_available=False,
    )
    assignment = reg.assignment()
    for agent, expected in _default_assignment().items():
        assert assignment[agent] == expected


def test_build_applies_explicit_assignment(tmp_path):
    """The assignment passed in IS the resolved assignment (modulo policy snap)."""
    assignment = _default_assignment()
    assignment["career_advisor"] = {"provider": "claude", "model": "claude-haiku-4-5-20251001"}
    reg = ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=False)
    assert reg.assignment()["career_advisor"]["model"] == "claude-haiku-4-5-20251001"


def test_build_falls_back_when_openai_unavailable(tmp_path):
    """A pick targeting openai when key missing falls back to a Claude model."""
    assignment = _default_assignment()
    assignment["career_advisor"] = {"provider": "openai", "model": "gpt-4o"}
    reg = ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=False)
    # Falls back to a Claude model from the catalog.
    assert reg.assignment()["career_advisor"]["provider"] == "claude"


def test_build_rejects_unknown_model_for_known_provider(tmp_path):
    assignment = _default_assignment()
    assignment["research_agent"] = {"provider": "claude", "model": "claude-future-model"}
    with pytest.raises(UnknownModelError):
        ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=False)


def test_build_rejects_unknown_provider(tmp_path):
    assignment = _default_assignment()
    assignment["research_agent"] = {"provider": "bogus", "model": "claude-sonnet-4-6"}
    with pytest.raises(UnknownModelError):
        ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=False)


def test_for_agent_returns_correct_provider_class(tmp_path):
    reg = ModelRegistry.build(
        _loader(tmp_path), _catalog(), _default_assignment(), openai_available=False,
    )
    assert isinstance(reg.for_agent("research_agent"), ClaudeProvider)


def test_for_agent_returns_openai_when_assigned(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    assignment = _default_assignment()
    assignment["research_agent"] = {"provider": "openai", "model": "gpt-4o-mini"}
    reg = ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=True)
    assert isinstance(reg.for_agent("research_agent"), OpenAIProvider)


def test_provider_instances_shared_across_same_model(tmp_path):
    """Two agents with the same (provider, model) get the same instance — no duplication."""
    reg = ModelRegistry.build(
        _loader(tmp_path), _catalog(), _default_assignment(), openai_available=False,
    )
    # research_agent and scoring_agent both run Haiku in the default assignment.
    assert reg.for_agent("research_agent") is reg.for_agent("scoring_agent")


def test_catalog_for_ui_marks_openai_unavailable_when_no_key():
    out = ModelRegistry.catalog_for_ui(_catalog(), openai_available=False)
    assert out["openai"]["available"] is False
    assert out["claude"]["available"] is True
    assert all("input_per_m" in m for m in out["claude"]["models"])


def test_catalog_for_ui_includes_cost_cap_metadata():
    out = ModelRegistry.catalog_for_ui(_catalog(), openai_available=False)
    meta = out.get("_meta") or {}
    assert set(meta.get("high_volume_agents") or []) == set(HIGH_VOLUME_AGENTS)
    assert set(meta.get("high_volume_safe_models") or []) == set(HIGH_VOLUME_SAFE_MODELS)


# ── Cost-cap guardrail (policy invariant — stays in code) ────────────────────

def test_high_volume_agents_are_research_and_scoring():
    """Sanity: the cap covers exactly the per-job, multi-call agents."""
    assert HIGH_VOLUME_AGENTS == frozenset({"research_agent", "scoring_agent"})


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
    """A pick pinning scoring_agent to Sonnet must be reverted to a safe model,
    with a warning. This is the durable line of defense — direct DB writes that
    bypass the UI's filtering still get caught here.
    """
    import logging
    assignment = _default_assignment()
    assignment["scoring_agent"] = {"provider": "claude", "model": "claude-sonnet-4-6"}
    with caplog.at_level(logging.WARNING, logger="app.providers.model_registry"):
        reg = ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=False)
    # Snapped back to a safe model.
    assert reg.assignment()["scoring_agent"]["model"] in HIGH_VOLUME_SAFE_MODELS
    # Warning logged with the agent name.
    assert "scoring_agent" in caplog.text
    assert "high-volume" in caplog.text


def test_build_keeps_high_volume_agent_when_assigned_safe_alternative(tmp_path, monkeypatch):
    """gpt-4o-mini is in the allowlist (cheaper than Haiku) — the override sticks."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    assignment = _default_assignment()
    assignment["research_agent"] = {"provider": "openai", "model": "gpt-4o-mini"}
    reg = ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=True)
    assert reg.assignment()["research_agent"]["model"] == "gpt-4o-mini"


def test_build_does_not_constrain_low_volume_agents(tmp_path):
    """career_advisor and friends remain freely configurable — the cap targets
    the multi-call-per-job agents only.
    """
    assignment = _default_assignment()
    assignment["career_advisor"] = {"provider": "claude", "model": "claude-opus-4-7"}
    reg = ModelRegistry.build(_loader(tmp_path), _catalog(), assignment, openai_available=False)
    assert reg.assignment()["career_advisor"]["model"] == "claude-opus-4-7"
