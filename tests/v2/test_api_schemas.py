"""Tests for API request/response Pydantic schemas."""
from __future__ import annotations

from app.api.schemas.requests import StartWorkflowRequest


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
