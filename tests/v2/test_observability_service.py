"""Tests for ObservabilityService — repo delegation and failure isolation."""
from unittest.mock import MagicMock, patch

import pytest

from app.services.observability_service import ObservabilityService
from app.state.workflow_state import WorkflowStep


def _svc(obs=None, steps=None, decisions=None, security=None):
    return ObservabilityService(
        observability_repo=obs or MagicMock(),
        step_repo=steps or MagicMock(),
        decision_repo=decisions or MagicMock(),
        security_repo=security or MagicMock(),
    )


# ── log_agent_started ─────────────────────────────────────────────────────────

def test_log_agent_started_returns_event_id():
    svc = _svc()
    event_id = svc.log_agent_started("wf-001", "scoring_agent", "3 jobs")
    assert isinstance(event_id, str)
    assert len(event_id) == 36  # UUID


def test_log_agent_started_calls_repo():
    obs = MagicMock()
    svc = _svc(obs=obs)
    svc.log_agent_started("wf-001", "scoring_agent", "3 jobs")
    obs.create_agent_event.assert_called_once()
    call_kwargs = obs.create_agent_event.call_args[1]
    assert call_kwargs["event_type"] == "started"
    assert call_kwargs["status"] == "running"
    assert call_kwargs["agent_name"] == "scoring_agent"


def test_log_agent_started_truncates_long_summary():
    obs = MagicMock()
    svc = _svc(obs=obs)
    svc.log_agent_started("wf-001", "agent", "x" * 1000)
    call_kwargs = obs.create_agent_event.call_args[1]
    assert len(call_kwargs["input_summary"]) <= 500


# ── log_agent_completed ───────────────────────────────────────────────────────

def test_log_agent_completed_calls_repo():
    obs = MagicMock()
    svc = _svc(obs=obs)
    svc.log_agent_completed("wf-001", "scoring_agent", "evt-001", "3 scores", 420)
    obs.create_agent_event.assert_called_once()
    call_kwargs = obs.create_agent_event.call_args[1]
    assert call_kwargs["event_type"] == "completed"
    assert call_kwargs["duration_ms"] == 420


# ── log_agent_failed ──────────────────────────────────────────────────────────

def test_log_agent_failed_calls_repo():
    obs = MagicMock()
    svc = _svc(obs=obs)
    svc.log_agent_failed("wf-001", "scoring_agent", "evt-001", "timeout", 300)
    call_kwargs = obs.create_agent_event.call_args[1]
    assert call_kwargs["event_type"] == "failed"
    assert call_kwargs["status"] == "failed"


# ── log_llm_call ──────────────────────────────────────────────────────────────

def test_log_llm_call_calls_repo():
    obs = MagicMock()
    svc = _svc(obs=obs)
    svc.log_llm_call("wf-001", "scoring_agent", "anthropic", "claude-haiku-4-5-20251001",
                     1000, 200, 0.0003, 350)
    obs.create_llm_call.assert_called_once()
    call_kwargs = obs.create_llm_call.call_args[1]
    assert call_kwargs["tokens_input"] == 1000
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


# ── log_step_started ──────────────────────────────────────────────────────────

def test_log_step_started_returns_step_execution_id():
    svc = _svc()
    step_id = svc.log_step_started("wf-001", WorkflowStep.SCORING)
    assert isinstance(step_id, str)
    assert len(step_id) == 36


def test_log_step_started_calls_step_repo():
    steps = MagicMock()
    svc = _svc(steps=steps)
    svc.log_step_started("wf-001", WorkflowStep.SCORING)
    steps.create.assert_called_once()
    args = steps.create.call_args[0]
    assert args[2] == "scoring"  # step.value


# ── log_step_completed / failed ───────────────────────────────────────────────

def test_log_step_completed_calls_step_repo():
    steps = MagicMock()
    svc = _svc(steps=steps)
    svc.log_step_completed("wf-001", "step-id-001", 420, notes="done")
    steps.complete.assert_called_once_with("step-id-001", notes="done")


def test_log_step_failed_calls_step_repo():
    steps = MagicMock()
    svc = _svc(steps=steps)
    svc.log_step_failed("wf-001", "step-id-001", notes="timeout")
    steps.fail.assert_called_once_with("step-id-001", notes="timeout")


# ── log_security_event ────────────────────────────────────────────────────────

def test_log_security_event_calls_security_repo():
    security = MagicMock()
    svc = _svc(security=security)
    svc.log_security_event("wf-001", "prompt_injection", "high", "Injection detected in JD")
    security.create.assert_called_once()
    call_kwargs = security.create.call_args[1]
    assert call_kwargs["severity"] == "high"
    assert call_kwargs["event_type"] == "prompt_injection"


# ── log_human_decision ────────────────────────────────────────────────────────

def test_log_human_decision_calls_decision_repo():
    decisions = MagicMock()
    svc = _svc(decisions=decisions)
    svc.log_human_decision(
        "wf-001", "select_jobs", "confirmed",
        {"selected": ["job-001"]},
        "2026-04-29T10:00:00.000Z", "2026-04-29T10:05:00.000Z",
    )
    decisions.create.assert_called_once()


# ── Failure isolation ─────────────────────────────────────────────────────────

def test_repo_failure_does_not_propagate():
    obs = MagicMock()
    obs.create_agent_event.side_effect = RuntimeError("DB locked")
    svc = _svc(obs=obs)
    # Must not raise — observability failures are swallowed
    svc.log_agent_started("wf-001", "agent", "input")


def test_step_repo_failure_does_not_propagate():
    steps = MagicMock()
    steps.create.side_effect = RuntimeError("disk full")
    svc = _svc(steps=steps)
    result = svc.log_step_started("wf-001", WorkflowStep.SCORING)
    assert isinstance(result, str)  # still returns a step_execution_id


def test_security_repo_failure_does_not_propagate():
    security = MagicMock()
    security.create.side_effect = RuntimeError("DB error")
    svc = _svc(security=security)
    svc.log_security_event("wf-001", "injection", "high", "details")  # must not raise
