"""Tests for TailoringAgent."""

import pytest
from unittest.mock import MagicMock

from app.agents.tailoring_agent import TailoringAgent
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.tailored_resume_draft import TailoredResumeDraft, TailoredBullet
from app.services.observability_service import ObservabilityService


def _draft_result() -> dict:
    return {
        "job_id": "job-001", "resume_id": "res-001",
        "summary_suggestions": [
            {
                "original_text": "Software engineer with 8 years experience.",
                "suggested_text": "Platform engineer with 8 years building distributed systems at scale.",
                "supporting_evidence": "Led Kubernetes migration in experience section.",
                "claim_type": "reword",
                "fidelity_risk": "low",
                "unsupported_claims": [],
            }
        ],
        "experience_bullet_suggestions": [
            {
                "original_text": "Maintained internal tooling.",
                "suggested_text": "Built developer tooling adopted by 3 teams.",
                "supporting_evidence": "Resume mentions 'internal tooling' and team usage.",
                "claim_type": "emphasize",
                "fidelity_risk": "low",
                "unsupported_claims": [],
            }
        ],
        "skills_section_suggestions": ["Add: Distributed Systems"],
        "overall_tailoring_notes": "Emphasise platform scope.",
        "fidelity_risk_summary": "Low overall risk. One gap clearly labelled.",
    }

def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _draft_result()
    return mock

def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

_CONTEXT = {
    "job_id": "job-001", "resume_id": "res-001",
    "job_description": "Staff Engineer.",
    "resume_profile": {"name": "Jane", "skills": ["Python"]},
    "final_review": {},
    "career_advice": {},
}


def test_run_returns_tailored_resume_draft_instance():
    result = TailoringAgent(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result, TailoredResumeDraft)

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    TailoringAgent(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "tailoring_agent"

def test_bullets_have_supporting_evidence():
    result = TailoringAgent(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    for bullet in result.experience_bullet_suggestions:
        assert bullet.supporting_evidence, "Every bullet must carry supporting evidence"

def test_gap_bullets_are_present_in_result():
    # Gaps must be labelled — not silently dropped or rewritten.
    gap_bullet = {
        "original_text": "", "suggested_text": "Led team of 5 engineers.",
        "supporting_evidence": "No leadership experience found in resume.",
        "claim_type": "gap", "fidelity_risk": "high", "unsupported_claims": ["team leadership"],
    }
    result_with_gap = {**_draft_result(), "experience_bullet_suggestions": [gap_bullet]}
    provider = _make_provider(result_with_gap)
    result = TailoringAgent(provider, _make_obs()).run("wf-001", _CONTEXT)
    gap_items = [b for b in result.experience_bullet_suggestions if b.claim_type == "gap"]
    assert len(gap_items) == 1

def test_run_never_passes_raw_resume_text():
    provider = _make_provider()
    ctx = {**_CONTEXT, "resume_profile": {"name": "Jane"}}
    TailoringAgent(provider, _make_obs()).run("wf-001", ctx)
    passed = provider.complete.call_args.kwargs["context"]
    assert isinstance(passed["resume_profile"], dict)

def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    TailoringAgent(_make_provider(), obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    with pytest.raises(LLMProviderError):
        TailoringAgent(provider, _make_obs()).run("wf-001", _CONTEXT)
