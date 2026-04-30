"""Tests for ResearchAgent."""

import pytest
from unittest.mock import MagicMock

from app.agents.research_agent import ResearchAgent
from app.providers.llm_client import LLMClient, LLMProviderError
from app.schemas.research_context import ResearchContext
from app.services.observability_service import ObservabilityService


def _research_result() -> dict:
    return {
        "job_id": "job-001",
        "company_summary": "Mid-stage SaaS company, ~400 engineers.",
        "role_context": "Platform engineering team, reports to VP Eng.",
        "technology_signals": ["Python", "Kubernetes", "GCP"],
        "leadership_signals": ["cross-team coordination"],
        "domain_signals": ["fintech"],
        "risk_flags": [],
        "research_steps": [
            {"step_number": 1, "tool_used": "job_page_fetcher", "observation_summary": "JD analyzed."}
        ],
        "confidence": 72,
    }

def _make_provider(result=None):
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = result or _research_result()
    return mock

def _make_obs():
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs

_CONTEXT = {
    "job_id": "job-001", "job_title": "Staff Engineer",
    "company": "Acme", "source_url": "https://example.com/job/1",
    "job_description": "Python, Kubernetes, distributed systems.",
}


def test_run_returns_research_context_instance():
    agent = ResearchAgent(_make_provider(), _make_obs())
    result = agent.run("wf-001", _CONTEXT)
    assert isinstance(result, ResearchContext)

def test_run_populates_technology_signals():
    agent = ResearchAgent(_make_provider(), _make_obs())
    result = agent.run("wf-001", _CONTEXT)
    assert "Python" in result.technology_signals

def test_run_calls_provider_with_correct_agent_name():
    provider = _make_provider()
    ResearchAgent(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["agent_name"] == "research_agent"

def test_run_passes_schema_to_provider():
    provider = _make_provider()
    ResearchAgent(provider, _make_obs()).run("wf-001", _CONTEXT)
    assert provider.complete.call_args.kwargs["schema"] is ResearchContext

def test_run_emits_started_and_completed_events():
    obs = _make_obs()
    ResearchAgent(_make_provider(), obs).run("wf-001", _CONTEXT)
    obs.log_agent_started.assert_called_once()
    obs.log_agent_completed.assert_called_once()

def test_run_emits_failed_event_on_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("fail")
    obs = _make_obs()
    with pytest.raises(LLMProviderError):
        ResearchAgent(provider, obs).run("wf-001", _CONTEXT)
    obs.log_agent_failed.assert_called_once()

def test_run_propagates_llm_provider_error():
    provider = _make_provider()
    provider.complete.side_effect = LLMProviderError("network error")
    with pytest.raises(LLMProviderError, match="network error"):
        ResearchAgent(provider, _make_obs()).run("wf-001", _CONTEXT)
