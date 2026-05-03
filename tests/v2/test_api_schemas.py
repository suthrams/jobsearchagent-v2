"""Tests for API request/response Pydantic schemas."""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.schemas.requests import (
    DecisionRequest,
    JobSelectionDecision,
    StartWorkflowRequest,
    TailoringDecision,
)
from app.workflows.limits import MAX_SELECTED_JOBS


# ── StartWorkflowRequest ──────────────────────────────────────────────────────

def test_start_workflow_request_defaults():
    req = StartWorkflowRequest(resume_id="res-001", search_criteria={"roles": ["SWE"]})
    assert req.workflow_type == "full_career_review"
    assert req.effective_config == {}


def test_start_workflow_request_explicit_fields():
    req = StartWorkflowRequest(
        resume_id="res-002",
        search_criteria={"roles": ["EM"]},
        workflow_type="management_track",
        effective_config={"scoring": {"career_track": "management"}},
    )
    assert req.workflow_type == "management_track"
    assert req.effective_config["scoring"]["career_track"] == "management"


# ── JobSelectionDecision ──────────────────────────────────────────────────────

def test_job_selection_decision_valid():
    dec = JobSelectionDecision(
        decision_type="select_jobs_for_deep_review",
        selected_job_ids=["job-001", "job-002"],
    )
    assert dec.decision_type == "select_jobs_for_deep_review"
    assert len(dec.selected_job_ids) == 2


def test_job_selection_decision_empty_list_fails():
    with pytest.raises(ValidationError):
        JobSelectionDecision(
            decision_type="select_jobs_for_deep_review",
            selected_job_ids=[],
        )


def test_job_selection_decision_too_many_jobs_fails():
    too_many = [f"job-{i:03d}" for i in range(1, MAX_SELECTED_JOBS + 2)]
    with pytest.raises(ValidationError):
        JobSelectionDecision(
            decision_type="select_jobs_for_deep_review",
            selected_job_ids=too_many,
        )


def test_job_selection_decision_max_at_cap():
    at_cap = [f"job-{i:03d}" for i in range(1, MAX_SELECTED_JOBS + 1)]
    dec = JobSelectionDecision(
        decision_type="select_jobs_for_deep_review",
        selected_job_ids=at_cap,
    )
    assert len(dec.selected_job_ids) == MAX_SELECTED_JOBS


# ── TailoringDecision ─────────────────────────────────────────────────────────

def test_tailoring_decision_valid_approve():
    dec = TailoringDecision(decision_type="approve_tailoring", approval="approve")
    assert dec.approval == "approve"


def test_tailoring_decision_valid_revise():
    dec = TailoringDecision(decision_type="approve_tailoring", approval="revise")
    assert dec.approval == "revise"


def test_tailoring_decision_valid_reject():
    dec = TailoringDecision(decision_type="approve_tailoring", approval="reject")
    assert dec.approval == "reject"


def test_tailoring_decision_invalid_approval_fails():
    with pytest.raises(ValidationError):
        TailoringDecision(decision_type="approve_tailoring", approval="maybe")


# ── DecisionRequest discriminator ─────────────────────────────────────────────

_adapter = TypeAdapter(DecisionRequest)


def test_decision_request_routes_to_job_selection():
    raw = {
        "decision_type": "select_jobs_for_deep_review",
        "selected_job_ids": ["job-001"],
    }
    result = _adapter.validate_python(raw)
    assert isinstance(result, JobSelectionDecision)


def test_decision_request_routes_to_tailoring():
    raw = {"decision_type": "approve_tailoring", "approval": "approve"}
    result = _adapter.validate_python(raw)
    assert isinstance(result, TailoringDecision)


def test_decision_request_unknown_type_fails():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"decision_type": "unknown_type"})
