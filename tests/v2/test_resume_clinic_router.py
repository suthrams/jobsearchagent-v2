"""Integration tests for the Resume Clinic router (ADR-066 Phase 4).

Exercises:
  POST /users/{id}/resume-clinic
  GET  /users/{id}/resume-clinic
  POST /resume-clinic/{review_id}/decisions

Uses dependency overrides so no real LangGraph, ConfigService, or DB is touched.
Mocks the runner inputs (resume repo + clinic repo + reviewer + fidelity) and
asserts wiring + decision flow. The runner itself is unit-tested in
test_resume_clinic_runner.py; this file focuses on the HTTP surface.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_deps, get_graph
from app.api.main import app
from app.repositories.database import utcnow_iso
from app.schemas.fidelity_review import FidelityReview
from app.schemas.resume_clinic import ResumeClinicReview
from app.workflows.workflow_graph import WorkflowDependencies


USER_ID = "0"
RESUME_ID = "res-clinic-001"
OTHER_RESUME_ID = "res-clinic-other"


def _review() -> ResumeClinicReview:
    return ResumeClinicReview(
        quality={
            "dimensions": [
                {"dimension": "structure_ordering", "rating": "adequate",
                 "findings": ["projects buried"], "fixes": ["promote projects"]},
            ],
            "overall_summary": "Solid foundation; quantification and reorder would help.",
        },
        alignment={
            "fit_summary": "moderate fit",
            "missing_skills": ["AWS"],
            "missing_keywords": [],
            "suggested_certifications": [],
            "suggested_projects": [],
            "emphasize": [],
            "confidence": "medium",
        },
        reorganization={"section_order": ["summary", "experience"], "moves": []},
        rewrites=[
            {
                "section_label": "experience:Acme:Engineer",
                "original_text": "Worked on backend systems.",
                "suggested_text": "Designed and shipped a backend service handling 200 RPS.",
                "claim_type": "quantify",
                "supporting_evidence": "Resume mentions backend role.",
            },
        ],
    )


def _fidelity() -> FidelityReview:
    return FidelityReview(
        job_id="clinic:x",
        resume_id=RESUME_ID,
        overall_fidelity_status="pass",
        unsupported_claims=[],
        fabricated_metrics=[],
        inflated_scope_flags=[],
        unsupported_technology_flags=[],
        unsupported_certification_flags=[],
        required_removals=[],
        required_revisions=[],
        approval_recommendation="approve",
        confidence=90,
    )


def _make_resume_row(user_id=USER_ID, resume_id=RESUME_ID):
    return {
        "id": resume_id,
        "user_id": user_id,
        "raw_text": "Software engineer with 5 years experience.",
        "parsed_profile_json": json.dumps({
            "name": "Test User",
            "skills": ["Python"],
            "experience": [],
        }),
    }


def _make_deps() -> WorkflowDependencies:
    # Reviewer + fidelity agents (mocked)
    reviewer = MagicMock()
    reviewer.run.return_value = _review()
    fidelity = MagicMock()
    fidelity.run.return_value = _fidelity()

    # Resume repo with two resumes (one owned by USER_ID, one orphaned to "7")
    resumes = {
        RESUME_ID: _make_resume_row(),
        OTHER_RESUME_ID: _make_resume_row(user_id="7", resume_id=OTHER_RESUME_ID),
    }
    resume_repo = MagicMock()
    resume_repo.get_by_id.side_effect = lambda rid: resumes.get(rid)
    resume_repo.get_active.side_effect = lambda uid: (
        resumes[RESUME_ID] if str(uid) == USER_ID else None
    )

    # In-memory clinic repo so the router can persist and read back.
    clinic_store: dict[str, dict] = {}

    def _clinic_create(clinic_id, user_id, resume_id, *, workflow_run_id,
                       target_role, target_track, seniority_aware,
                       review, alignment, overhaul, fidelity_review):
        clinic_store[clinic_id] = {
            "id": clinic_id,
            "user_id": user_id,
            "resume_id": resume_id,
            "workflow_run_id": workflow_run_id,
            "target_role": target_role,
            "target_track": target_track,
            "seniority_aware": bool(seniority_aware),
            "review": review,
            "alignment": alignment,
            "overhaul": overhaul,
            "fidelity_review": fidelity_review,
            "decision": None,
            "edited": None,
            "decided_at": None,
            "created_at": utcnow_iso(),
        }

    def _clinic_get(clinic_id):
        return clinic_store.get(clinic_id)

    def _clinic_list(user_id):
        return [r for r in clinic_store.values() if r["user_id"] == str(user_id)]

    def _clinic_decision(clinic_id, decision, edited=None):
        if clinic_id in clinic_store:
            clinic_store[clinic_id]["decision"] = decision
            clinic_store[clinic_id]["decided_at"] = utcnow_iso()
            clinic_store[clinic_id]["edited"] = edited

    clinic_repo = MagicMock()
    clinic_repo.create.side_effect = _clinic_create
    clinic_repo.get_by_id.side_effect = _clinic_get
    clinic_repo.list_by_user.side_effect = _clinic_list
    clinic_repo.set_decision.side_effect = _clinic_decision

    # Workflow repo: no-op create/update; get_by_status returns empty.
    workflow_repo = MagicMock()
    workflow_repo.create.return_value = None
    workflow_repo.update_state.return_value = None
    workflow_repo.get_by_status.return_value = []
    workflow_repo.get_by_id.return_value = None

    return WorkflowDependencies(
        research_agent=MagicMock(),
        scoring_agent=MagicMock(),
        resume_critic=MagicMock(),
        review_auditor=MagicMock(),
        career_advisor=MagicMock(),
        interview_coach=MagicMock(),
        tailoring_agent=MagicMock(),
        fidelity_reviewer=fidelity,
        resume_reviewer=reviewer,
        discovery_service=MagicMock(),
        resume_parser=MagicMock(),
        report_generator=MagicMock(),
        job_repo=MagicMock(),
        score_repo=MagicMock(),
        advice_repo=MagicMock(),
        review_repo=MagicMock(),
        tailoring_repo=MagicMock(),
        resume_clinic_repo=clinic_repo,
        workflow_repo=workflow_repo,
        resume_repo=resume_repo,
        observability=MagicMock(),
        checkpointer=MagicMock(),
    )


@pytest.fixture
def client():
    """Pre-built deps so each test can read the same in-memory clinic_store
    via the bound side-effects. Graph dependency is overridden to a MagicMock."""
    deps = _make_deps()
    app.dependency_overrides[get_deps] = lambda: deps
    app.dependency_overrides[get_graph] = lambda: MagicMock()
    yield TestClient(app), deps
    app.dependency_overrides.clear()


# ── POST /users/{id}/resume-clinic ──────────────────────────────────────────

def test_post_clinic_success_with_target(client):
    c, _ = client
    resp = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={
            "resume_id": RESUME_ID,
            "target_role": "security analyst",
            "target_track": "ic",
            "seniority_aware": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clinic_id"]
    assert body["user_id"] == USER_ID
    assert body["resume_id"] == RESUME_ID
    assert body["target_role"] == "security analyst"
    assert body["target_track"] == "ic"
    assert body["seniority_aware"] is True
    assert body["quality"]["overall_summary"].startswith("Solid")
    assert body["alignment"]["confidence"] == "medium"
    assert body["overhaul"]["rewrites"][0]["claim_type"] == "quantify"
    assert body["fidelity_review"]["approval_recommendation"] == "approve"


def test_post_clinic_quality_only_when_no_target(client):
    c, deps = client
    # Reviewer that returns alignment=None
    deps.resume_reviewer.run.return_value = ResumeClinicReview(
        quality={
            "dimensions": [
                {"dimension": "clarity", "rating": "strong",
                 "findings": [], "fixes": []},
            ],
            "overall_summary": "Clean.",
        },
        alignment=None,
        reorganization={"section_order": ["summary"], "moves": []},
        rewrites=[],
    )
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": RESUME_ID,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_role"] is None
    assert body["target_track"] is None
    assert body["alignment"] is None


def test_post_clinic_uses_active_resume_when_resume_id_omitted(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["resume_id"] == RESUME_ID


def test_post_clinic_404_unknown_resume(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": "does-not-exist",
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "resume_not_found"


def test_post_clinic_404_when_using_other_users_resume(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": OTHER_RESUME_ID,
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "resume_not_found"


def test_post_clinic_422_invalid_target_track(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": RESUME_ID,
        "target_track": "sales",
    })
    assert resp.status_code == 422
    # Our main.py normalizes Pydantic validation errors to {error, message, details}.
    detail = resp.json()["detail"]
    assert detail["error"] == "validation_error"


# ── GET /users/{id}/resume-clinic ────────────────────────────────────────────

def test_get_clinic_lists_user_runs_newest_first(client):
    c, _ = client
    # Seed two runs.
    c.post(f"/users/{USER_ID}/resume-clinic", json={"resume_id": RESUME_ID})
    c.post(f"/users/{USER_ID}/resume-clinic", json={"resume_id": RESUME_ID})
    resp = c.get(f"/users/{USER_ID}/resume-clinic")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == USER_ID
    assert len(body["reviews"]) == 2


def test_get_clinic_empty_for_user_with_no_runs(client):
    c, _ = client
    resp = c.get(f"/users/{USER_ID}/resume-clinic")
    assert resp.status_code == 200, resp.text
    assert resp.json()["reviews"] == []


# ── POST /resume-clinic/{id}/decisions ──────────────────────────────────────

def test_decision_approve_records_state(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(f"/resume-clinic/{clinic_id}/decisions",
                  json={"approval": "approve"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["decided_at"] is not None
    assert body["edited"] is None


def test_decision_edit_persists_edited(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    edited_payload = {"reorganization": {"section_order": ["x"], "moves": []}, "rewrites": []}
    resp = c.post(
        f"/resume-clinic/{clinic_id}/decisions",
        json={"approval": "edit", "edited": edited_payload},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "edit"
    assert body["edited"] == edited_payload


def test_decision_edit_requires_edited_payload(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(f"/resume-clinic/{clinic_id}/decisions",
                  json={"approval": "edit"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "validation_error"


def test_decision_404_when_review_unknown(client):
    c, _ = client
    resp = c.post("/resume-clinic/does-not-exist/decisions",
                  json={"approval": "approve"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "clinic_review_not_found"


def test_decision_invalid_value_422(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(f"/resume-clinic/{clinic_id}/decisions",
                  json={"approval": "maybe"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "validation_error"
