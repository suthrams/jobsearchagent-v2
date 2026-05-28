"""Repository tests for resume_clinic_reviews — ADR-066 Phase 1.

The repo mirrors the tailoring repo's pattern: create, get_by_id, list_by_user,
set_decision. JSON columns are persisted as strings; rows come back with the
parsed JSON attached under flat keys (review, alignment, overhaul, ...) and the
seniority_aware int cast to a bool.
"""
from __future__ import annotations

import time

import pytest

from app.repositories.database import init_db
from app.repositories.resume_clinic_repository import ResumeClinicRepository


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


@pytest.fixture
def repo(db_path):
    return ResumeClinicRepository(db_path)


def _sample_review() -> dict:
    return {
        "dimensions": [
            {"dimension": "structure_ordering", "rating": "adequate",
             "findings": ["projects buried"], "fixes": ["promote projects above experience"]},
        ],
        "overall_summary": "solid resume; minor restructuring would help.",
    }


def _sample_overhaul() -> dict:
    return {
        "reorganization": {
            "section_order": ["summary", "projects", "experience", "education"],
            "moves": [
                {"action": "promote", "subject": "Projects", "rationale": "stronger signal for early-career"}
            ],
        },
        "rewrites": [
            {
                "section_label": "Experience",
                "original_text": "Worked on backend systems.",
                "suggested_text": "Designed and shipped a backend service handling 200 RPS.",
                "claim_type": "quantify",
                "supporting_evidence": "Resume mentions a backend role; throughput inferred from the team-size context.",
            }
        ],
    }


def _sample_alignment() -> dict:
    return {
        "fit_summary": "moderate fit; resume light on cloud breadth.",
        "missing_skills": ["AWS", "Kubernetes"],
        "missing_keywords": ["distributed systems"],
        "suggested_certifications": ["AWS SAA"],
        "suggested_projects": ["personal portfolio service on EKS"],
        "emphasize": ["systems experience"],
        "confidence": "medium",
    }


# ─── create / get_by_id ──────────────────────────────────────────────────────

def test_create_and_get_by_id(repo):
    repo.create(
        clinic_id="cl-1", user_id="0", resume_id="r-1",
        workflow_run_id="wf-1",
        target_role="entry-level security analyst", target_track="ic",
        seniority_aware=True,
        review=_sample_review(),
        alignment=_sample_alignment(),
        overhaul=_sample_overhaul(),
        fidelity_review={"verdict": "pass", "issues": []},
    )
    row = repo.get_by_id("cl-1")
    assert row is not None
    assert row["id"] == "cl-1"
    assert row["user_id"] == "0"
    assert row["resume_id"] == "r-1"
    assert row["workflow_run_id"] == "wf-1"
    assert row["target_role"] == "entry-level security analyst"
    assert row["target_track"] == "ic"
    assert row["seniority_aware"] is True
    assert row["review"]["overall_summary"].startswith("solid resume")
    assert row["alignment"]["confidence"] == "medium"
    assert row["overhaul"]["rewrites"][0]["claim_type"] == "quantify"
    assert row["fidelity_review"]["verdict"] == "pass"
    assert row["decision"] is None
    assert row["edited"] is None


def test_get_by_id_returns_none_for_unknown_id(repo):
    assert repo.get_by_id("does-not-exist") is None


def test_create_with_no_target_persists_null_alignment(repo):
    repo.create(
        clinic_id="cl-2", user_id="0", resume_id="r-1",
        workflow_run_id=None,
        target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(),
        alignment=None,
        overhaul=_sample_overhaul(),
        fidelity_review=None,
    )
    row = repo.get_by_id("cl-2")
    assert row is not None
    assert row["alignment"] is None
    assert row["fidelity_review"] is None
    assert row["seniority_aware"] is False
    assert row["workflow_run_id"] is None
    assert row["target_role"] is None
    assert row["target_track"] is None


# ─── list_by_user ────────────────────────────────────────────────────────────

def test_list_by_user_returns_newest_first(repo):
    repo.create(
        clinic_id="cl-old", user_id="0", resume_id="r-1",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    # SQLite timestamps are millisecond-precision; sleep just long enough for ordering.
    time.sleep(0.01)
    repo.create(
        clinic_id="cl-new", user_id="0", resume_id="r-1",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    rows = repo.list_by_user("0")
    assert [r["id"] for r in rows] == ["cl-new", "cl-old"]


def test_list_by_user_returns_empty_for_unknown_user(repo):
    repo.create(
        clinic_id="cl-x", user_id="0", resume_id="r-1",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    assert repo.list_by_user("999") == []


def test_list_by_user_scoping_excludes_other_users(repo):
    repo.create(
        clinic_id="cl-u0", user_id="0", resume_id="r-1",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    repo.create(
        clinic_id="cl-u1", user_id="1", resume_id="r-2",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    u0_ids = {r["id"] for r in repo.list_by_user("0")}
    u1_ids = {r["id"] for r in repo.list_by_user("1")}
    assert u0_ids == {"cl-u0"}
    assert u1_ids == {"cl-u1"}


# ─── set_decision ────────────────────────────────────────────────────────────

def test_set_decision_approve_records_decision_and_timestamp(repo):
    repo.create(
        clinic_id="cl-3", user_id="0", resume_id="r-1",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    repo.set_decision("cl-3", "approve")
    row = repo.get_by_id("cl-3")
    assert row["decision"] == "approve"
    assert row["decided_at"] is not None
    assert row["edited"] is None


def test_set_decision_edit_persists_edited_json(repo):
    repo.create(
        clinic_id="cl-4", user_id="0", resume_id="r-1",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    edited = {
        "reorganization": {"section_order": ["education", "projects", "experience"], "moves": []},
        "rewrites": [
            {
                "section_label": "Experience",
                "original_text": "Worked on backend systems.",
                "suggested_text": "Owned a backend service in Go (custom wording the human prefers).",
                "claim_type": "reframe",
                "supporting_evidence": "human edit",
            }
        ],
    }
    repo.set_decision("cl-4", "edit", edited=edited)
    row = repo.get_by_id("cl-4")
    assert row["decision"] == "edit"
    assert row["edited"]["rewrites"][0]["suggested_text"].startswith("Owned a backend")
    # The agent's original overhaul is left intact for the audit trail.
    assert row["overhaul"]["rewrites"][0]["claim_type"] == "quantify"


def test_set_decision_reject_does_not_persist_edited_json(repo):
    repo.create(
        clinic_id="cl-5", user_id="0", resume_id="r-1",
        workflow_run_id=None, target_role=None, target_track=None,
        seniority_aware=False,
        review=_sample_review(), alignment=None,
        overhaul=_sample_overhaul(), fidelity_review=None,
    )
    repo.set_decision("cl-5", "reject")
    row = repo.get_by_id("cl-5")
    assert row["decision"] == "reject"
    assert row["edited"] is None
