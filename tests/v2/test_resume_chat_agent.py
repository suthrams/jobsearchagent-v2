"""Tests for ResumeChatAgent (ADR-068).

Mirrors the resume_reviewer agent test shape: mock the provider, assert the
contract, never test prose. Plus:
  - the agent is registered in config.yaml + config.example.yaml
  - the agent has a pin entry in tests/model_pins.json so the build-time
    invariant from commit e31cee1 covers it
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import ValidationError

from app.agents.resume_chat import ResumeChatAgent
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.resume_chat import ResumeChatTurnResult
from app.services.observability_service import ObservabilityService


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _turn_result(*, reply: str = "Trimmed the summary by two sentences.",
                 changed_sections: list[str] | None = None,
                 rewrites_count: int = 1) -> dict:
    rewrites = []
    for i in range(rewrites_count):
        rewrites.append({
            "section_label": f"experience:Acme:Engineer#{i}",
            "original_text": "Worked on backend systems.",
            "suggested_text": f"Designed and shipped backend service #{i}.",
            "claim_type": "restate",
            "supporting_evidence": "Resume mentions backend role.",
        })
    return {
        "reply": reply,
        "overhaul": {
            "reorganization": {
                "section_order": ["summary", "experience", "skills"],
                "moves": [],
            },
            "rewrites": rewrites,
        },
        "changed_sections": changed_sections if changed_sections is not None
                            else ["experience"],
    }


def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result if result is not None else _turn_result()
    return mock


def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-chat-001"
    return obs


_CONTEXT = {
    "_cached": {"resume_profile": {"name": "Jane", "skills": ["Python"]}},
    "resume_id": "res-1",
    "current_overhaul": {"reorganization": {"section_order": [], "moves": []},
                         "rewrites": []},
    "history": [],
    "section": "whole",
    "message": "Make the summary shorter and front-load the cybersecurity angle.",
}


# ── Contract ─────────────────────────────────────────────────────────────────

def test_run_returns_resume_chat_turn_result_instance():
    result = ResumeChatAgent(_make_provider(), _make_obs()).run("wf-chat", _CONTEXT)
    assert isinstance(result, ResumeChatTurnResult)


def test_run_calls_provider_with_correct_agent_name_and_schema():
    provider = _make_provider()
    ResumeChatAgent(provider, _make_obs()).run("wf-chat", _CONTEXT)
    kwargs = provider.complete.call_args.kwargs
    assert kwargs["agent_name"] == "resume_chat"
    assert kwargs["schema"] is ResumeChatTurnResult


def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    ResumeChatAgent(_make_provider(), obs).run("wf-chat", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()


def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("upstream timeout")
    with pytest.raises(LLMProviderError):
        ResumeChatAgent(provider, _make_obs()).run("wf-chat", _CONTEXT)


def test_run_passes_parsed_profile_not_raw_text():
    """Same prompt rule the reviewer follows - raw_text never reaches the
    chat agent. The Fidelity Reviewer is the only consumer of raw_text."""
    provider = _make_provider()
    ResumeChatAgent(provider, _make_obs()).run("wf-chat", _CONTEXT)
    ctx = provider.complete.call_args.kwargs["context"]
    assert "raw_text" not in ctx
    # Parsed profile lives under the cached block.
    assert "resume_profile" in (ctx.get("_cached") or {})


# ── Schema invariants ───────────────────────────────────────────────────────

def test_empty_reply_fails_validation():
    bad = _turn_result()
    bad["reply"] = ""
    with pytest.raises(ValidationError):
        ResumeChatTurnResult(**bad)


def test_oversized_reply_fails_validation():
    bad = _turn_result()
    bad["reply"] = "x" * 700
    with pytest.raises(ValidationError):
        ResumeChatTurnResult(**bad)


def test_unknown_changed_section_fails_validation():
    bad = _turn_result()
    bad["changed_sections"] = ["alignment"]   # not in the Literal set
    with pytest.raises(ValidationError):
        ResumeChatTurnResult(**bad)


def test_rewrite_with_empty_supporting_evidence_fails_validation():
    bad = _turn_result()
    bad["overhaul"]["rewrites"][0]["supporting_evidence"] = ""
    with pytest.raises(ValidationError):
        ResumeChatTurnResult(**bad)


def test_empty_changed_sections_is_valid():
    """Off-topic / no-op turns return the overhaul unchanged with an empty
    changed_sections list. That's valid."""
    result = _turn_result(changed_sections=[], rewrites_count=0)
    parsed = ResumeChatTurnResult(**result)
    assert parsed.changed_sections == []


# ── Registration ─────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_resume_chat_registered_in_config_yaml():
    cfg = yaml.safe_load((_REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    agents = cfg.get("agents") or {}
    assert "resume_chat" in agents, "ADR-068: resume_chat must be in config.yaml"


def test_resume_chat_registered_in_config_example_yaml():
    cfg = yaml.safe_load((_REPO_ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    agents = cfg.get("agents") or {}
    assert "resume_chat" in agents


def test_resume_chat_pinned_in_model_pins_json():
    raw = json.loads((_REPO_ROOT / "tests" / "model_pins.json").read_text(encoding="utf-8"))
    pins = raw.get("pins") or {}
    assert "resume_chat" in pins, (
        "ADR-068: resume_chat must be added to tests/model_pins.json so the "
        "build-time invariant covers it."
    )
