"""Tests for InterviewCoach."""

import pytest
from unittest.mock import MagicMock

from app.agents.interview_coach import InterviewCoach
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.interview_prep import InterviewPrep
from app.services.observability_service import ObservabilityService


def _prep_result() -> dict:
    return {
        "job_id": "job-001",
        "likely_interview_topics": ["System design", "Distributed consensus", "Mentoring"],
        "technical_topics_to_review": ["Raft protocol", "CAP theorem"],
        "leadership_stories_to_prepare": ["Led migration to Kubernetes"],
        "weak_areas_to_defend": ["Limited people management"],
        "questions_to_ask_interviewer": ["What does success look like in 90 days?"],
        "seven_day_prep_plan": ["Day 1: review system design patterns"],
        "confidence": 82,
    }

def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _prep_result()
    return mock

def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

_CONTEXT = {
    "job_id": "job-001",
    "job_description": "Staff Engineer.",
    "resume_profile": {"name": "Jane"},
    "job_score": {"overall_score": 85},
    "research_context": {},
    "career_advice": {},
    "final_review": {},
}


def test_run_returns_interview_prep_instance():
    result = InterviewCoach(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result, InterviewPrep)

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    InterviewCoach(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "interview_coach"

def test_run_result_contains_prep_plan():
    result = InterviewCoach(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert len(result.seven_day_prep_plan) > 0

def test_run_result_identifies_weak_areas():
    result = InterviewCoach(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert len(result.weak_areas_to_defend) > 0

def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    InterviewCoach(_make_provider(), obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()

def test_run_emits_failed_event_on_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        InterviewCoach(provider, obs).run("wf-001", _CONTEXT)
    obs.log_agent_failed.assert_called_once()

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("internal server error")
    with pytest.raises(LLMProviderError):
        InterviewCoach(provider, _make_obs()).run("wf-001", _CONTEXT)
