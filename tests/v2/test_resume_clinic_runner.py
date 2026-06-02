"""Runner tests for the Resume Clinic (ADR-066 Phase 3).

The runner is the out-of-graph orchestrator: load resume -> role-data lookup
-> ResumeReviewerAgent -> FidelityReviewer on rewrites -> persist via
ResumeClinicRepository, plus a lightweight workflow_runs row for cost
attribution.

Tests stub the agents and exercise the runner's wiring + invariants:
- Fidelity ALWAYS runs when there are rewrites (the ADR-066 invariant)
- Fidelity is SKIPPED when there are no rewrites (nothing to police)
- A lightweight workflow_runs row is written for cost attribution
- Ownership: unknown resume / wrong-user resume -> ResumeClinicError
- raw_text is NEVER in the reviewer context (prompt rule)
- target_role/track flow through to the reviewer context and the persisted row
- Alignment-null is preserved into the persisted row
- Role-data provider is consulted; None proceeds gracefully
- A reviewer LLM failure flips the workflow row to failed and re-raises
- A fidelity LLM failure persists the row with fidelity_review_json=null
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.providers.llm_client import LLMProviderError
from app.repositories.database import init_db
from app.repositories.resume_clinic_repository import ResumeClinicRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.fidelity_review import FidelityReview
from app.schemas.resume_clinic import ResumeClinicReview
from app.services.resume_clinic_runner import ResumeClinicError, run_clinic
from app.services.role_data import NullRoleDataProvider, RoleData


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_clinic.db"
    init_db(path)
    return path


@pytest.fixture
def repos(db_path):
    resume_repo = ResumeRepository(db_path)
    clinic_repo = ResumeClinicRepository(db_path)
    workflow_repo = WorkflowRepository(db_path)
    return resume_repo, clinic_repo, workflow_repo


def _seed_resume(resume_repo, user_id="0", resume_id="r-1",
                 raw_text="Software engineer with 5 years backend.",
                 profile=None) -> None:
    profile = profile or {"name": "Jane", "skills": ["Python"], "experience": []}
    resume_repo.create(resume_id, user_id, "jane.pdf", raw_text, profile)


def _review_with_rewrites(*, alignment=None) -> ResumeClinicReview:
    return ResumeClinicReview(
        quality={
            "dimensions": [
                {"dimension": "structure_ordering", "rating": "adequate",
                 "findings": ["projects buried"], "fixes": ["promote projects"]},
            ],
            "overall_summary": "Solid foundation.",
        },
        alignment=alignment,
        reorganization={"section_order": ["summary", "experience"], "moves": []},
        rewrites=[
            {
                "section_label": "experience:Acme:Engineer",
                "original_text": "Worked on backend systems.",
                "suggested_text": "Designed and shipped a backend service handling 200 RPS.",
                "claim_type": "quantify",
                "supporting_evidence": "Resume mentions backend role and team usage.",
            },
        ],
    )


def _review_without_rewrites() -> ResumeClinicReview:
    return ResumeClinicReview(
        quality={
            "dimensions": [
                {"dimension": "structure_ordering", "rating": "strong",
                 "findings": [], "fixes": []},
            ],
            "overall_summary": "Clean resume.",
        },
        alignment=None,
        reorganization={"section_order": ["summary", "experience"], "moves": []},
        rewrites=[],
    )


def _fidelity_pass(job_id="clinic:x", resume_id="r-1") -> FidelityReview:
    return FidelityReview(
        job_id=job_id,
        resume_id=resume_id,
        overall_fidelity_status="pass",
        unsupported_claims=[],
        fabricated_metrics=[],
        inflated_scope_flags=[],
        unsupported_technology_flags=[],
        unsupported_certification_flags=[],
        required_removals=[],
        required_revisions=[],
        approval_recommendation="approve",
        confidence=95,
    )


def _make_reviewer(result):
    m = MagicMock()
    m.run.return_value = result
    return m


def _make_fidelity(result):
    m = MagicMock()
    m.run.return_value = result
    return m


# ── Persistence + ownership ──────────────────────────────────────────────────

def test_run_clinic_loads_resume_and_persists_review(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    row = run_clinic(
        user_id="0", resume_id="r-1",
        target_role=None, target_track=None, seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    assert row["id"]
    assert row["user_id"] == "0"
    assert row["resume_id"] == "r-1"
    assert row["workflow_run_id"]
    persisted = clinic_repo.get_by_id(row["id"])
    assert persisted is not None
    assert persisted["overhaul"]["rewrites"][0]["claim_type"] == "quantify"


def test_run_clinic_raises_on_unknown_resume(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    with pytest.raises(ResumeClinicError):
        run_clinic(
            user_id="0", resume_id="missing",
            target_role=None, target_track=None, seniority_aware=False,
            resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
            reviewer=MagicMock(), fidelity=MagicMock(), role_data=NullRoleDataProvider(), observability=MagicMock(),
        )


def test_run_clinic_raises_on_resume_owned_by_different_user(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo, user_id="7", resume_id="r-7")
    with pytest.raises(ResumeClinicError):
        run_clinic(
            user_id="0", resume_id="r-7",
            target_role=None, target_track=None, seniority_aware=False,
            resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
            reviewer=MagicMock(), fidelity=MagicMock(), role_data=NullRoleDataProvider(), observability=MagicMock(),
        )


# ── Cost attribution: lightweight workflow_runs row ──────────────────────────

def test_run_clinic_writes_lightweight_workflow_runs_row(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    row = run_clinic(
        user_id="0", resume_id="r-1",
        target_role=None, target_track=None, seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    wf = workflow_repo.get_by_id(row["workflow_run_id"])
    assert wf is not None
    assert wf["workflow_type"] == "resume_clinic"
    assert wf["user_id"] == "0"
    assert wf["status"] == "completed"


# ── Fidelity invariant ───────────────────────────────────────────────────────

def test_run_clinic_always_runs_fidelity_when_rewrites_exist(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    run_clinic(
        user_id="0", resume_id="r-1",
        target_role=None, target_track=None, seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    fidelity.run.assert_called_once()
    # The fidelity prompt expects a tailored_draft envelope.
    ctx = fidelity.run.call_args.args[1]
    assert "tailored_draft" in ctx
    assert ctx["job_id"].startswith("clinic:")


def test_run_clinic_skips_fidelity_when_no_rewrites(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_without_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    row = run_clinic(
        user_id="0", resume_id="r-1",
        target_role=None, target_track=None, seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    fidelity.run.assert_not_called()
    assert row["fidelity_review"] is None


# ── Prompt rule: raw_text never reaches the reviewer ─────────────────────────

def test_run_clinic_never_passes_raw_text_to_reviewer(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    run_clinic(
        user_id="0", resume_id="r-1",
        target_role=None, target_track=None, seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    ctx = reviewer.run.call_args.args[1]
    flat = json.dumps(ctx)
    assert "raw_text" not in ctx
    assert "Software engineer with 5 years backend" not in flat, (
        "raw_text content must not leak into the reviewer context"
    )


# ── Targets + null alignment ─────────────────────────────────────────────────

def test_run_clinic_passes_target_role_and_track_to_reviewer(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    run_clinic(
        user_id="0", resume_id="r-1",
        target_role="security analyst", target_track="ic", seniority_aware=True,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    ctx = reviewer.run.call_args.args[1]
    assert ctx["target_role"] == "security analyst"
    assert ctx["target_track"] == "ic"
    assert ctx["seniority_aware"] is True


def test_run_clinic_persists_target_role_and_seniority_on_row(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    row = run_clinic(
        user_id="0", resume_id="r-1",
        target_role="security analyst", target_track="ic", seniority_aware=True,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    assert row["target_role"] == "security analyst"
    assert row["target_track"] == "ic"
    assert row["seniority_aware"] is True


def test_run_clinic_persists_null_alignment_when_reviewer_returns_none(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites(alignment=None))
    fidelity = _make_fidelity(_fidelity_pass())
    row = run_clinic(
        user_id="0", resume_id="r-1",
        target_role=None, target_track=None, seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    assert row["alignment"] is None


# ── RoleDataProvider seam ────────────────────────────────────────────────────

def test_run_clinic_consults_role_data_provider(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    provider = MagicMock()
    provider.lookup.return_value = RoleData(
        occupation_title="Security Analyst",
        required_skills=["SIEM", "network defense"],
        source="fake",
    )
    run_clinic(
        user_id="0", resume_id="r-1",
        target_role="security analyst", target_track="ic", seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=provider, observability=MagicMock(),
    )
    provider.lookup.assert_called_once_with("security analyst", "ic")
    ctx = reviewer.run.call_args.args[1]
    assert ctx["role_data"]["occupation_title"] == "Security Analyst"


def test_run_clinic_role_data_provider_returning_none_proceeds(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = _make_fidelity(_fidelity_pass())
    run_clinic(
        user_id="0", resume_id="r-1",
        target_role="security analyst", target_track="ic", seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    ctx = reviewer.run.call_args.args[1]
    assert ctx["role_data"] is None


# ── Failure paths ────────────────────────────────────────────────────────────

def test_run_clinic_reviewer_failure_flips_workflow_to_failed_and_reraises(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = MagicMock()
    reviewer.run.side_effect = LLMProviderError("upstream timeout")
    fidelity = MagicMock()
    with pytest.raises(LLMProviderError):
        run_clinic(
            user_id="0", resume_id="r-1",
            target_role=None, target_track=None, seniority_aware=False,
            resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
            reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
        )
    # A workflow_runs row was created at startup with status="running"; it
    # should be flipped to "failed" on the reviewer error.
    failed_rows = workflow_repo.get_by_status("failed")
    assert any(r["workflow_type"] == "resume_clinic" for r in failed_rows)
    # No clinic row should have been persisted.
    assert clinic_repo.list_by_user("0") == []


def test_run_clinic_fidelity_failure_persists_row_with_null_fidelity(repos):
    resume_repo, clinic_repo, workflow_repo = repos
    _seed_resume(resume_repo)
    reviewer = _make_reviewer(_review_with_rewrites())
    fidelity = MagicMock()
    fidelity.run.side_effect = LLMProviderError("upstream timeout")
    row = run_clinic(
        user_id="0", resume_id="r-1",
        target_role=None, target_track=None, seniority_aware=False,
        resume_repo=resume_repo, clinic_repo=clinic_repo, workflow_repo=workflow_repo,
        reviewer=reviewer, fidelity=fidelity, role_data=NullRoleDataProvider(), observability=MagicMock(),
    )
    assert row["fidelity_review"] is None
    # The clinic should still be marked as a completed workflow run; the user
    # still gets the quality + alignment + overhaul, just without the fidelity
    # verdict.
    wf = workflow_repo.get_by_id(row["workflow_run_id"])
    assert wf["status"] == "completed"
