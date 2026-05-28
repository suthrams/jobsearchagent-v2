"""Tests for ResumeReviewerAgent (ADR-066 Phase 2).

Pattern mirrors the other agent tests: mock provider, assert contract, never
test prose. Includes:
  - schema returns a ResumeClinicReview instance (not a dict)
  - agent_name passed to the provider is "resume_reviewer"
  - alignment is null when no target given
  - rewrites with empty supporting_evidence fail schema validation
  - quality dimension Literal rejects unknown values
  - parsed profile is passed, raw resume text is not
  - the agent is registered in config.yaml
  - the agent has a pin in tests/model_pins.json
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import ValidationError

from app.agents.resume_reviewer import ResumeReviewerAgent
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.resume_clinic import ResumeClinicReview
from app.services.observability_service import ObservabilityService


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _review_result(*, with_alignment: bool = True) -> dict:
    rewrite = {
        "section_label": "experience:Acme:Engineer",
        "original_text": "Worked on backend systems.",
        "suggested_text": "Designed and shipped a backend service handling 200 RPS.",
        "claim_type": "quantify",
        "supporting_evidence": "Resume mentions a backend role; throughput inferred from team-size context.",
    }
    result = {
        "quality": {
            "dimensions": [
                {
                    "dimension": "structure_ordering",
                    "rating": "adequate",
                    "findings": ["projects buried"],
                    "fixes": ["promote projects above experience"],
                },
                {
                    "dimension": "impact_quantification",
                    "rating": "needs_work",
                    "findings": ["bullets describe activities, not outcomes"],
                    "fixes": ["add a metric to each experience bullet"],
                },
            ],
            "overall_summary": "Solid foundation; the biggest wins are quantification and reordering.",
        },
        "alignment": None,
        "reorganization": {
            "section_order": ["summary", "projects", "experience", "education", "skills"],
            "moves": [
                {"action": "promote", "subject": "Projects", "rationale": "stronger early-career signal"},
            ],
        },
        "rewrites": [rewrite],
    }
    if with_alignment:
        result["alignment"] = {
            "fit_summary": "moderate fit; resume light on cloud breadth.",
            "missing_skills": ["AWS", "Kubernetes"],
            "missing_keywords": ["distributed systems"],
            "suggested_certifications": ["AWS SAA"],
            "suggested_projects": ["EKS portfolio service"],
            "emphasize": ["systems experience"],
            "confidence": "medium",
        }
    return result


def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result if result is not None else _review_result()
    return mock


def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-clinic-001"
    return obs


_CONTEXT = {
    "resume_id": "res-001",
    "resume_profile": {"name": "Jane", "skills": ["Python"], "experience": []},
    "target_role": "entry-level security analyst",
    "target_track": "ic",
    "seniority_aware": True,
    "role_data": None,
}


# ── Contract: agent returns a validated schema ───────────────────────────────

def test_run_returns_resume_clinic_review_instance():
    result = ResumeReviewerAgent(_make_provider(), _make_obs()).run("wf-clinic", _CONTEXT)
    assert isinstance(result, ResumeClinicReview)


def test_run_calls_provider_with_correct_agent_name_and_schema():
    provider = _make_provider()
    ResumeReviewerAgent(provider, _make_obs()).run("wf-clinic", _CONTEXT)
    kwargs = provider.complete.call_args.kwargs
    assert kwargs["agent_name"] == "resume_reviewer"
    assert kwargs["schema"] is ResumeClinicReview


def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    ResumeReviewerAgent(_make_provider(), obs).run("wf-clinic", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()


def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    with pytest.raises(LLMProviderError):
        ResumeReviewerAgent(provider, _make_obs()).run("wf-clinic", _CONTEXT)


# ── Alignment is nullable ────────────────────────────────────────────────────

def test_run_emits_alignment_as_none_when_provider_returns_null():
    provider = _make_provider(_review_result(with_alignment=False))
    result = ResumeReviewerAgent(provider, _make_obs()).run("wf-clinic", _CONTEXT)
    assert result.alignment is None


def test_run_emits_alignment_when_provider_returns_one():
    result = ResumeReviewerAgent(_make_provider(), _make_obs()).run("wf-clinic", _CONTEXT)
    assert result.alignment is not None
    assert result.alignment.confidence == "medium"


# ── Schema invariants ────────────────────────────────────────────────────────

def test_rewrite_with_empty_supporting_evidence_fails_validation():
    bad = _review_result()
    bad["rewrites"][0]["supporting_evidence"] = ""
    with pytest.raises(ValidationError):
        ResumeClinicReview(**bad)


def test_unknown_quality_dimension_fails_validation():
    bad = _review_result()
    bad["quality"]["dimensions"][0]["dimension"] = "made_up_dimension"
    with pytest.raises(ValidationError):
        ResumeClinicReview(**bad)


def test_unknown_quality_rating_fails_validation():
    bad = _review_result()
    bad["quality"]["dimensions"][0]["rating"] = "ok"
    with pytest.raises(ValidationError):
        ResumeClinicReview(**bad)


def test_unknown_reorganization_action_fails_validation():
    bad = _review_result()
    bad["reorganization"]["moves"][0]["action"] = "swap"
    with pytest.raises(ValidationError):
        ResumeClinicReview(**bad)


def test_unknown_rewrite_claim_type_fails_validation():
    bad = _review_result()
    bad["rewrites"][0]["claim_type"] = "ad_lib"
    with pytest.raises(ValidationError):
        ResumeClinicReview(**bad)


def test_unknown_alignment_confidence_fails_validation():
    bad = _review_result()
    bad["alignment"]["confidence"] = "probably"
    with pytest.raises(ValidationError):
        ResumeClinicReview(**bad)


# ── PII / prompt rule: parsed profile only, never raw text ───────────────────

def test_run_passes_parsed_profile_not_raw_text():
    provider = _make_provider()
    ResumeReviewerAgent(provider, _make_obs()).run("wf-clinic", _CONTEXT)
    ctx = provider.complete.call_args.kwargs["context"]
    assert "resume_profile" in ctx
    assert isinstance(ctx["resume_profile"], dict)
    assert "raw_text" not in ctx, "raw resume text must never be sent to the reviewer"


# ── Registration: config.yaml + model pin ────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_resume_reviewer_registered_in_config_yaml():
    cfg = yaml.safe_load((_REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    agents = cfg.get("agents") or {}
    assert "resume_reviewer" in agents, "Phase 2 must register resume_reviewer in config.yaml"
    entry = agents["resume_reviewer"]
    assert entry.get("provider") == "claude"
    assert isinstance(entry.get("model"), str) and entry["model"]


def test_resume_reviewer_registered_in_config_example_yaml():
    cfg = yaml.safe_load((_REPO_ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    agents = cfg.get("agents") or {}
    assert "resume_reviewer" in agents


def test_resume_reviewer_pinned_in_model_pins_json():
    raw = json.loads((_REPO_ROOT / "tests" / "model_pins.json").read_text(encoding="utf-8"))
    pins = raw.get("pins") or {}
    assert "resume_reviewer" in pins, (
        "ADR-058 / pin invariant: every new agent's (provider, model) must be "
        "added to tests/model_pins.json so a swap cannot land silently."
    )
    pin = pins["resume_reviewer"]
    assert pin["provider"] == "claude"
    assert pin["model"]
