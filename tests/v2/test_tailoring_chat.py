"""ADR-072 T3: POST /tailorings/{id}/chat-session.

The endpoint seeds a clinic chat session from a job's tailored draft and anchors
it to the originating run + job. Session creation has no LLM call, so this uses
real repos on a temp DB (no agent mocks needed) with a lightweight get_deps
override carrying just the four repos the endpoint touches.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_deps
from app.api.main import app
from app.repositories.database import init_db
from app.repositories.resume_clinic_repository import ResumeClinicRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.workflow_repository import WorkflowRepository

USER_ID = "0"
RESUME_ID = "res-1"
JOB_ID = "job-1"
SEARCH_RUN_ID = "search-run-1"
TAILORING_ID = "tail-1"


def _tailored_draft() -> dict:
    return {
        "job_id": JOB_ID, "resume_id": RESUME_ID,
        "summary_suggestions": [{
            "original_text": "Backend engineer.",
            "suggested_text": "Backend engineer who ships distributed services.",
            "supporting_evidence": "Resume summary mentions backend work.",
            "claim_type": "reword", "fidelity_risk": "low",
            "section_label": "summary", "impact_rationale": "matches JD",
        }],
        "experience_bullet_suggestions": [{
            "original_text": "Did X.", "suggested_text": "",
            "supporting_evidence": "n/a", "claim_type": "gap",
            "fidelity_risk": "low", "section_label": "experience:Acme:Eng",
            "impact_rationale": "JD wants Y",
        }],
    }


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    resume_repo = ResumeRepository(db)
    tailoring_repo = TailoringRepository(db)
    clinic_repo = ResumeClinicRepository(db)
    workflow_repo = WorkflowRepository(db)

    resume_repo.create(RESUME_ID, USER_ID, "r.pdf", "raw text",
                       {"name": "T", "skills": ["Python"]})
    workflow_repo.create(SEARCH_RUN_ID, "full_career_review",
                         {"status": "completed", "user_id": USER_ID})
    tailoring_repo.create(TAILORING_ID, SEARCH_RUN_ID, JOB_ID, RESUME_ID,
                          _tailored_draft(), fidelity_review={"overall_fidelity_status": "pass"})

    deps = SimpleNamespace(
        tailoring_repo=tailoring_repo,
        resume_clinic_repo=clinic_repo,
        resume_repo=resume_repo,
        workflow_repo=workflow_repo,
    )
    app.dependency_overrides[get_deps] = lambda: deps
    yield TestClient(app), clinic_repo
    app.dependency_overrides.clear()


def test_creates_session_seeded_from_draft(client):
    tc, clinic_repo = client
    resp = tc.post(f"/tailorings/{TAILORING_ID}/chat-session")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Seeded overhaul carries the reword bullet (gap dropped).
    rewrites = body["overhaul"]["rewrites"]
    assert len(rewrites) == 1
    assert rewrites[0]["claim_type"] == "restate"   # reword -> restate
    assert rewrites[0]["section_label"] == "summary"
    assert body["resume_id"] == RESUME_ID
    # Persisted row is anchored to the run + job.
    sessions = clinic_repo.list_by_job(SEARCH_RUN_ID, JOB_ID)
    assert len(sessions) == 1
    assert sessions[0]["job_id"] == JOB_ID
    assert sessions[0]["source_workflow_run_id"] == SEARCH_RUN_ID


def test_reuses_existing_session_on_second_call(client):
    tc, clinic_repo = client
    first = tc.post(f"/tailorings/{TAILORING_ID}/chat-session").json()
    second = tc.post(f"/tailorings/{TAILORING_ID}/chat-session").json()
    assert first["clinic_id"] == second["clinic_id"]
    assert len(clinic_repo.list_by_job(SEARCH_RUN_ID, JOB_ID)) == 1


def test_404_for_unknown_tailoring(client):
    tc, _ = client
    assert tc.post("/tailorings/nope/chat-session").status_code == 404
