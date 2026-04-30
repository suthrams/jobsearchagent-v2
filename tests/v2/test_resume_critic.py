"""Tests for ResumeCritic."""

import pytest
from unittest.mock import MagicMock

from app.agents.resume_critic import ResumeCritic
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.resume_review import ResumeReview
from app.services.observability_service import ObservabilityService


def _review_result() -> dict:
    return {
        "job_id": "job-001", "resume_id": "res-001",
        "overall_fit_summary": "Good technical fit, leadership section is thin.",
        "section_reviews": [
            {
                "section_name": "Experience",
                "current_issue": "Lacks distributed systems scope",
                "why_it_matters": "Role requires platform-scale thinking",
                "improvement_opportunity": "Add scale metrics",
                "suggested_direction": "Mention system throughput",
                "evidence": "No scale numbers in current bullets",
                "risk_level": "medium",
            }
        ],
        "critical_gaps": ["No mention of on-call experience"],
        "resume_only_gaps": ["Scale metrics missing from experience bullets"],
        "career_gaps_observed": [],
        "suggested_improvements": ["Add throughput numbers"],
        "questions_for_user": ["What was peak RPS for your main service?"],
        "confidence": 80,
    }

def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _review_result()
    return mock

def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

_CONTEXT = {
    "job_id": "job-001", "resume_id": "res-001",
    "job_description": "Staff Engineer role.",
    "resume_profile": {"name": "Jane", "skills": ["Python"]},
    "job_score": {"overall_score": 82},
    "research_context": {},
    "prior_audit_feedback": None,
    "review_round": 1,
}


def test_run_returns_resume_review_instance():
    result = ResumeCritic(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result, ResumeReview)

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    ResumeCritic(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "resume_critic"

def test_run_passes_review_round_in_context():
    provider = _make_provider()
    ctx = {**_CONTEXT, "review_round": 2, "prior_audit_feedback": "Be more specific."}
    ResumeCritic(provider, _make_obs()).run("wf-001", ctx)
    passed = provider.complete.call_args.kwargs["context"]
    assert passed["review_round"] == 2
    assert passed["prior_audit_feedback"] == "Be more specific."

def test_run_preserves_resume_career_gap_distinction():
    # resume_only_gaps and career_gaps_observed must stay separate — never conflated.
    result = ResumeCritic(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result.resume_only_gaps, list)
    assert isinstance(result.career_gaps_observed, list)

def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    ResumeCritic(_make_provider(), obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()

def test_run_emits_failed_event_on_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        ResumeCritic(provider, obs).run("wf-001", _CONTEXT)
    obs.log_agent_failed.assert_called_once()

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("repair failed")
    with pytest.raises(LLMProviderError):
        ResumeCritic(provider, _make_obs()).run("wf-001", _CONTEXT)
