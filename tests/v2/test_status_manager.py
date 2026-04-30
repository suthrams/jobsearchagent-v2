"""Tests for StatusManager — workflow and job status transition validation."""
import pytest

from app.services.status_manager import InvalidTransitionError, JobStatus, StatusManager
from app.state.workflow_state import WorkflowStatus

sm = StatusManager()


# ── Workflow transitions ──────────────────────────────────────────────────────

def test_workflow_initialized_to_running():
    result = sm.transition_workflow("wf-001", WorkflowStatus.INITIALIZED, WorkflowStatus.RUNNING)
    assert result == WorkflowStatus.RUNNING


def test_workflow_running_to_waiting_for_user():
    result = sm.transition_workflow("wf-001", WorkflowStatus.RUNNING, WorkflowStatus.WAITING_FOR_USER)
    assert result == WorkflowStatus.WAITING_FOR_USER


def test_workflow_waiting_for_user_to_running():
    result = sm.transition_workflow("wf-001", WorkflowStatus.WAITING_FOR_USER, WorkflowStatus.RUNNING)
    assert result == WorkflowStatus.RUNNING


def test_workflow_running_to_completed():
    result = sm.transition_workflow("wf-001", WorkflowStatus.RUNNING, WorkflowStatus.COMPLETED)
    assert result == WorkflowStatus.COMPLETED


def test_workflow_running_to_failed():
    result = sm.transition_workflow("wf-001", WorkflowStatus.RUNNING, WorkflowStatus.FAILED)
    assert result == WorkflowStatus.FAILED


def test_workflow_running_to_cancelled():
    result = sm.transition_workflow("wf-001", WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED)
    assert result == WorkflowStatus.CANCELLED


def test_workflow_completed_to_running_raises():
    with pytest.raises(InvalidTransitionError) as exc_info:
        sm.transition_workflow("wf-001", WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING)
    assert "wf-001" in str(exc_info.value)
    assert "completed" in str(exc_info.value)


def test_workflow_failed_to_running_raises():
    with pytest.raises(InvalidTransitionError):
        sm.transition_workflow("wf-001", WorkflowStatus.FAILED, WorkflowStatus.RUNNING)


def test_workflow_cancelled_to_running_raises():
    with pytest.raises(InvalidTransitionError):
        sm.transition_workflow("wf-001", WorkflowStatus.CANCELLED, WorkflowStatus.RUNNING)


def test_workflow_initialized_to_completed_raises():
    with pytest.raises(InvalidTransitionError):
        sm.transition_workflow("wf-001", WorkflowStatus.INITIALIZED, WorkflowStatus.COMPLETED)


def test_error_message_includes_workflow_id_and_statuses():
    with pytest.raises(InvalidTransitionError) as exc_info:
        sm.transition_workflow("wf-xyz", WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING)
    msg = str(exc_info.value)
    assert "wf-xyz" in msg
    assert "completed" in msg
    assert "running" in msg


# ── Workflow terminal checks ───────────────────────────────────────────────────

def test_is_terminal_workflow_completed():
    assert sm.is_terminal_workflow(WorkflowStatus.COMPLETED) is True


def test_is_terminal_workflow_failed():
    assert sm.is_terminal_workflow(WorkflowStatus.FAILED) is True


def test_is_terminal_workflow_cancelled():
    assert sm.is_terminal_workflow(WorkflowStatus.CANCELLED) is True


def test_is_not_terminal_workflow_running():
    assert sm.is_terminal_workflow(WorkflowStatus.RUNNING) is False


def test_is_not_terminal_workflow_waiting():
    assert sm.is_terminal_workflow(WorkflowStatus.WAITING_FOR_USER) is False


# ── Job transitions ───────────────────────────────────────────────────────────

def test_job_discovered_to_scored():
    assert sm.transition_job("job-1", JobStatus.DISCOVERED, JobStatus.SCORED) == JobStatus.SCORED


def test_job_scored_to_shortlisted():
    assert sm.transition_job("job-1", JobStatus.SCORED, JobStatus.SHORTLISTED) == JobStatus.SHORTLISTED


def test_job_scored_to_passed():
    assert sm.transition_job("job-1", JobStatus.SCORED, JobStatus.PASSED) == JobStatus.PASSED


def test_job_shortlisted_to_reviewed():
    assert sm.transition_job("job-1", JobStatus.SHORTLISTED, JobStatus.REVIEWED) == JobStatus.REVIEWED


def test_job_reviewed_to_applied():
    assert sm.transition_job("job-1", JobStatus.REVIEWED, JobStatus.APPLIED) == JobStatus.APPLIED


def test_job_reviewed_to_passed():
    assert sm.transition_job("job-1", JobStatus.REVIEWED, JobStatus.PASSED) == JobStatus.PASSED


def test_job_applied_to_rejected():
    assert sm.transition_job("job-1", JobStatus.APPLIED, JobStatus.REJECTED) == JobStatus.REJECTED


def test_job_applied_to_offer():
    assert sm.transition_job("job-1", JobStatus.APPLIED, JobStatus.OFFER) == JobStatus.OFFER


def test_job_shortlisted_to_discovered_raises():
    with pytest.raises(InvalidTransitionError) as exc_info:
        sm.transition_job("job-1", JobStatus.SHORTLISTED, JobStatus.DISCOVERED)
    assert "job-1" in str(exc_info.value)


def test_job_offer_to_applied_raises():
    with pytest.raises(InvalidTransitionError):
        sm.transition_job("job-1", JobStatus.OFFER, JobStatus.APPLIED)


def test_job_passed_to_any_raises():
    with pytest.raises(InvalidTransitionError):
        sm.transition_job("job-1", JobStatus.PASSED, JobStatus.SCORED)


def test_job_error_message_includes_job_id_and_statuses():
    with pytest.raises(InvalidTransitionError) as exc_info:
        sm.transition_job("job-xyz", JobStatus.OFFER, JobStatus.APPLIED)
    msg = str(exc_info.value)
    assert "job-xyz" in msg
    assert "offer" in msg
    assert "applied" in msg


# ── Job terminal checks ───────────────────────────────────────────────────────

def test_is_terminal_job_passed():
    assert sm.is_terminal_job(JobStatus.PASSED) is True


def test_is_terminal_job_rejected():
    assert sm.is_terminal_job(JobStatus.REJECTED) is True


def test_is_terminal_job_offer():
    assert sm.is_terminal_job(JobStatus.OFFER) is True


def test_is_not_terminal_job_reviewed():
    assert sm.is_terminal_job(JobStatus.REVIEWED) is False


def test_is_not_terminal_job_scored():
    assert sm.is_terminal_job(JobStatus.SCORED) is False
