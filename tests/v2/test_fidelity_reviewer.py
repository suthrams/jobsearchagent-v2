"""Tests for FidelityReviewer."""

import pytest
from unittest.mock import MagicMock

from app.agents.fidelity_reviewer import FidelityReviewer
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.fidelity_review import FidelityReview
from app.services.observability_service import ObservabilityService


def _fidelity_result(status="pass", recommendation="approve") -> dict:
    return {
        "job_id": "job-001", "resume_id": "res-001",
        "overall_fidelity_status": status,
        "unsupported_claims": [],
        "fabricated_metrics": [],
        "inflated_scope_flags": [],
        "unsupported_technology_flags": [],
        "unsupported_certification_flags": [],
        "required_removals": [],
        "required_revisions": [],
        "approval_recommendation": recommendation,
        "confidence": 90,
    }

def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _fidelity_result()
    return mock

def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

_CONTEXT = {
    "job_id": "job-001", "resume_id": "res-001",
    "job_description": "Staff Engineer.",
    "resume_profile": {"name": "Jane"},
    "tailored_draft": {"experience_bullet_suggestions": []},
}


def test_run_returns_fidelity_review_instance():
    result = FidelityReviewer(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert isinstance(result, FidelityReview)

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    FidelityReviewer(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "fidelity_reviewer"

def test_approved_draft_has_correct_recommendation():
    result = FidelityReviewer(_make_provider(), _make_obs()).run("wf-001", _CONTEXT)
    assert result.approval_recommendation == "approve"
    assert result.overall_fidelity_status == "pass"

def test_rejected_draft_sets_recommendation_to_reject():
    provider = _make_provider(_fidelity_result(status="fail", recommendation="reject"))
    result = FidelityReviewer(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert result.approval_recommendation == "reject"

def test_revision_draft_has_required_revisions():
    result_data = {
        **_fidelity_result(status="needs_revision", recommendation="revise"),
        "required_revisions": ["Remove '5x improvement' — no evidence"],
        "unsupported_claims": ["5x improvement claim"],
    }
    provider = _make_provider(result_data)
    result = FidelityReviewer(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert result.approval_recommendation == "revise"
    assert len(result.required_revisions) > 0

def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    FidelityReviewer(_make_provider(), obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()

def test_run_emits_failed_event_on_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        FidelityReviewer(provider, obs).run("wf-001", _CONTEXT)
    obs.log_agent_failed.assert_called_once()

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("rate limit")
    with pytest.raises(LLMProviderError):
        FidelityReviewer(provider, _make_obs()).run("wf-001", _CONTEXT)
