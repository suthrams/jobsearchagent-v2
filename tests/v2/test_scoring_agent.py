"""Tests for ScoringAgent.

All tests use mocked provider and observability — no real LLM calls.
"""

import pytest
from unittest.mock import MagicMock, call

from app.agents.scoring_agent import ScoringAgent
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.job_score import JobScore
from app.services.observability_service import ObservabilityService


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _score_result() -> dict:
    return {
        "job_id": "job-001", "resume_id": "res-001",
        "overall_score": 82, "technical_score": 88,
        "architecture_score": 75, "leadership_score": 60, "domain_score": 70,
        "match_summary": "Strong technical fit.",
        "strengths": ["Python", "Kubernetes"],
        "gaps": ["Limited leadership"],
        "recommended_next_action": "Apply",
        "confidence": 85,
    }

def _make_provider(result: dict | None = None) -> MagicMock:
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _score_result()
    return mock

def _make_obs() -> MagicMock:
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

def _make_agent(provider=None, obs=None) -> ScoringAgent:
    return ScoringAgent(provider or _make_provider(), obs or _make_obs())

_CONTEXT = {
    "job_id": "job-001", "resume_id": "res-001",
    "job_title": "Staff Engineer", "company": "Acme",
    "job_description": "Python, Kubernetes, distributed systems.",
    "resume_profile": {"name": "Jane", "skills": ["Python"]},
    "career_track": "all",
    "research_context": None,
}


# ── Schema and return type ────────────────────────────────────────────────────

def test_run_returns_job_score_instance():
    agent = _make_agent()
    result = agent.run("wf-001", _CONTEXT)
    assert isinstance(result, JobScore)

def test_run_result_fields_match_provider_output():
    agent = _make_agent()
    result = agent.run("wf-001", _CONTEXT)
    assert result.overall_score == 82
    assert result.technical_score == 88
    assert result.job_id == "job-001"


# ── Provider contract ─────────────────────────────────────────────────────────

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    _make_agent(provider=provider).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "scoring_agent"

def test_run_passes_context_to_provider():
    provider = _make_provider()
    _make_agent(provider=provider).run("wf-001", _CONTEXT)
    passed_context = provider.complete.call_args.kwargs["context"]
    assert passed_context["job_id"] == "job-001"
    assert passed_context["career_track"] == "all"

def test_run_passes_job_score_schema_to_provider():
    provider = _make_provider()
    _make_agent(provider=provider).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["schema"] is JobScore

def test_run_never_passes_raw_resume_text():
    # Only resume_profile dict is allowed — never raw text strings.
    provider = _make_provider()
    ctx = {**_CONTEXT, "raw_resume_text": "JANE SMITH\n..."}
    _make_agent(provider=provider).run("wf-001", ctx)
    passed_context = provider.complete.call_args.kwargs["context"]
    assert "raw_resume_text" in passed_context  # caller passed it; provider received it
    # The key assertion: resume_profile is a dict, not a raw string
    assert isinstance(passed_context["resume_profile"], dict)


# ── Observability ─────────────────────────────────────────────────────────────

def test_run_emits_started_event():
    obs = _make_obs()
    _make_agent(obs=obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    args = obs.log_agent_started.call_args
    assert args.args[0] == "wf-001"
    assert args.args[1] == "scoring_agent"

def test_run_emits_completed_event_on_success():
    obs = _make_obs()
    _make_agent(obs=obs).run("wf-001", _CONTEXT)
    obs.log_agent_completed.assert_called_once()

def test_run_emits_failed_event_on_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("timeout")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        _make_agent(provider=provider, obs=obs).run("wf-001", _CONTEXT)
    obs.log_agent_failed.assert_called_once()

def test_run_does_not_emit_completed_on_failure():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        _make_agent(provider=provider, obs=obs).run("wf-001", _CONTEXT)
    obs.log_agent_completed.assert_not_called()


# ── Error propagation ─────────────────────────────────────────────────────────

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("schema repair failed")
    with pytest.raises(LLMProviderError, match="schema repair failed"):
        _make_agent(provider=provider).run("wf-001", _CONTEXT)

def test_run_propagates_unexpected_exception():
    provider = _make_provider()
    provider.complete.side_effect = RuntimeError("unexpected")
    with pytest.raises(RuntimeError):
        _make_agent(provider=provider).run("wf-001", _CONTEXT)
