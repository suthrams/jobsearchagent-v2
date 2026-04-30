"""Tests for CareerAdvisor."""

import pytest
from unittest.mock import MagicMock

from app.agents.career_advisor import CareerAdvisor
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.career_advice import CareerAdvice
from app.services.observability_service import ObservabilityService


def _advice_result() -> dict:
    return {
        "job_id": "job-001",
        "positioning_summary": "Strong IC candidate, light on leadership proof points.",
        "resume_gaps": ["Leadership scope not visible in experience section"],
        "career_gaps": ["No direct people management experience"],
        "role_fit_assessment": "Good fit for senior IC, stretch for Staff.",
        "recommended_positioning": "Lead with distributed systems depth.",
        "skills_to_strengthen": ["System design at org level"],
        "experience_to_collect": ["Lead a cross-team technical initiative"],
        "thirty_sixty_ninety_day_plan": ["30d: assess team", "60d: ship first project"],
        "recommended_next_action": "Apply and address leadership gap in cover letter.",
        "confidence": 78,
    }

def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _advice_result()
    return mock

def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

_CONTEXT = {
    "job_id": "job-001", "resume_id": "res-001",
    "job_description": "Staff Engineer.",
    "resume_profile": {"name": "Jane"},
    "final_review": {"overall_fit_summary": "Good fit."},
    "job_score": {"overall_score": 82},
    "career_track": "ic",
}


def test_run_returns_career_advice_instance():
    result = CareerAdvisor(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result, CareerAdvice)

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    CareerAdvisor(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "career_advisor"

def test_resume_gaps_and_career_gaps_are_separate_lists():
    # This is a core invariant — the two must never be merged into one list.
    result = CareerAdvisor(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result.resume_gaps, list)
    assert isinstance(result.career_gaps, list)

def test_run_passes_career_track_in_context():
    provider = _make_provider()
    CareerAdvisor(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["context"]["career_track"] == "ic"

def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    CareerAdvisor(_make_provider(), obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()

def test_run_emits_failed_event_on_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        CareerAdvisor(provider, obs).run("wf-001", _CONTEXT)
    obs.log_agent_failed.assert_called_once()

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("connection error")
    with pytest.raises(LLMProviderError):
        CareerAdvisor(provider, _make_obs()).run("wf-001", _CONTEXT)
