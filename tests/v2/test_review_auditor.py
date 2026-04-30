"""Tests for ReviewAuditor."""

import pytest
from unittest.mock import MagicMock

from app.agents.review_auditor import ReviewAuditor
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.review_audit import ReviewAudit
from app.services.observability_service import ObservabilityService


def _audit_result(stop=False, score=78) -> dict:
    return {
        "job_id": "job-001", "round_number": 1,
        "audit_score": score,
        "auditor_confidence": 80,
        "quality_summary": "Critique is specific and evidence-based.",
        "missing_analysis_points": [],
        "generic_or_weak_feedback": [],
        "unsupported_claims": [],
        "fidelity_concerns": [],
        "recommended_revision_instructions": [],
        "stop_recommendation": stop,
        "stop_reason": "Quality threshold met." if stop else None,
    }

def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _audit_result()
    return mock

def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

_CONTEXT = {
    "job_id": "job-001",
    "resume_review": {"overall_fit_summary": "Good fit."},
    "resume_profile": {"name": "Jane"},
    "job_description": "Staff Engineer.",
    "job_score": {"overall_score": 82},
    "review_round": 1,
    "max_rounds": 3,
}


def test_run_returns_review_audit_instance():
    result = ReviewAuditor(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result, ReviewAudit)

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    ReviewAuditor(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "review_auditor"

def test_stop_recommendation_true_when_quality_met():
    provider = _make_provider(_audit_result(stop=True, score=90))
    result = ReviewAuditor(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert result.stop_recommendation is True
    assert result.stop_reason is not None

def test_stop_recommendation_false_on_low_score():
    provider = _make_provider(_audit_result(stop=False, score=45))
    result = ReviewAuditor(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert result.stop_recommendation is False

def test_run_passes_round_number_in_context():
    provider = _make_provider()
    ctx = {**_CONTEXT, "review_round": 2}
    ReviewAuditor(provider, _make_obs()).run("wf-001", ctx)
    assert provider.complete.call_args.kwargs["context"]["review_round"] == 2

def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    ReviewAuditor(_make_provider(), obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()

def test_run_emits_failed_event_on_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        ReviewAuditor(provider, obs).run("wf-001", _CONTEXT)
    obs.log_agent_failed.assert_called_once()

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("rate limit")
    with pytest.raises(LLMProviderError):
        ReviewAuditor(provider, _make_obs()).run("wf-001", _CONTEXT)
