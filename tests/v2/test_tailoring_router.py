"""Integration tests for the on-demand tailoring router.

Exercises POST /workflows/{wf}/jobs/{job}/tailorings (create), GET listings,
and the approve/revise/reject decision endpoint. Uses dependency overrides so
no real LangGraph or SqliteSaver is touched.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_deps, get_graph
from app.api.main import app
from app.repositories.advice_repository import AdviceRepository
from app.repositories.database import utcnow_iso
from app.repositories.job_repository import JobRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.schemas.fidelity_review import FidelityReview
from app.schemas.tailored_resume_draft import TailoredBullet, TailoredResumeDraft
from app.workflows.workflow_graph import WorkflowDependencies


WF_ID = "wf-tail-001"
JOB_ID = "job-tail-001"
RESUME_ID = "res-tail-001"


def _state_with_job() -> dict:
    return {
        "workflow_id": WF_ID,
        "resume_id": RESUME_ID,
        "resume_profile": {"name": "Test User", "skills": ["Python"]},
        "selected_jobs": [{
            "job_id": JOB_ID,
            "title": "Staff Engineer",
            "company": "Acme",
            "job_description": "Build distributed systems in Python.",
            "overall_score": 85,
        }],
    }


def _draft() -> TailoredResumeDraft:
    return TailoredResumeDraft(
        job_id=JOB_ID,
        resume_id=RESUME_ID,
        summary_suggestions=[
            TailoredBullet(
                original_text="Senior engineer with 10 years of experience.",
                suggested_text="Staff engineer with deep distributed-systems experience in Python.",
                supporting_evidence="Resume line: 'Designed and operated distributed Python services at scale.'",
                claim_type="reword",
                fidelity_risk="low",
                unsupported_claims=[],
            ),
        ],
        experience_bullet_suggestions=[
            TailoredBullet(
                original_text="Led platform team.",
                suggested_text="Led platform team owning Python-based distributed services serving 10M+ users.",
                supporting_evidence="Resume line: 'Led platform team for 3 years.'",
                claim_type="emphasize",
                fidelity_risk="low",
                unsupported_claims=[],
            ),
            TailoredBullet(
                original_text="",
                suggested_text="(GAP — never worked with Kafka)",
                supporting_evidence="No Kafka experience found in resume.",
                claim_type="gap",
                fidelity_risk="high",
                unsupported_claims=["Kafka"],
            ),
        ],
        skills_section_suggestions=["Distributed systems", "Python at scale"],
        overall_tailoring_notes="Two safe rewords plus one explicit gap.",
        fidelity_risk_summary="Low overall; one explicit gap flagged.",
    )


def _fidelity() -> FidelityReview:
    return FidelityReview(
        job_id=JOB_ID,
        resume_id=RESUME_ID,
        overall_fidelity_status="pass",
        unsupported_claims=[],
        fabricated_metrics=[],
        inflated_scope_flags=[],
        unsupported_technology_flags=["Kafka"],
        unsupported_certification_flags=[],
        required_removals=[],
        required_revisions=[],
        approval_recommendation="approve",
        confidence=92,
    )


def _make_graph_with_state(state: dict | None) -> MagicMock:
    """Build a mock graph whose get_state returns a snapshot with the given values."""
    graph = MagicMock()
    if state is None:
        snapshot = SimpleNamespace(values={}, tasks=[], next=None)
    else:
        snapshot = SimpleNamespace(values=state, tasks=[], next=None)
    graph.get_state.return_value = snapshot
    return graph


def _make_deps(graph) -> WorkflowDependencies:
    """Build WorkflowDependencies with mocked agents and repos for tailoring tests."""
    tailoring_agent = MagicMock()
    tailoring_agent.run.return_value = _draft()

    fidelity_reviewer = MagicMock()
    fidelity_reviewer.run.return_value = _fidelity()

    job_repo = MagicMock(spec=JobRepository)
    job_repo.get_by_id.return_value = None  # state is enough for happy path

    review_repo = MagicMock(spec=ReviewRepository)
    # The job is in selected_jobs, i.e. it was deep-reviewed — return a review so
    # the ADR-061 deep-review-on-demand path is skipped (that path is covered by
    # its own tests). An empty review_json parses to {} for tailoring context.
    review_repo.get_review_by_run_job.return_value = {"review_json": "{}"}

    advice_repo = MagicMock(spec=AdviceRepository)
    advice_repo.get_advice_by_run_job.return_value = None

    tailoring_repo = MagicMock(spec=TailoringRepository)
    # In-memory store keyed by tailoring_id
    store: dict[str, dict] = {}

    def _create(tid, wf_id, jid, rid, tailored, fidelity_review=None):
        store[tid] = {
            "id": tid, "workflow_run_id": wf_id, "job_id": jid, "resume_id": rid,
            "tailored": tailored, "fidelity_review": fidelity_review,
            "decision": None, "decided_at": None, "approved": 0, "edited": None,
            "created_at": utcnow_iso(),
        }

    def _get(tid):
        return store.get(tid)

    def _list(wf_id):
        return [r for r in store.values() if r["workflow_run_id"] == wf_id]

    def _set_decision(tid, decision, edited=None):
        if tid in store:
            store[tid]["decision"] = decision
            store[tid]["decided_at"] = utcnow_iso()
            store[tid]["approved"] = 1 if decision in ("approve", "edit") else 0
            store[tid]["edited"] = edited

    tailoring_repo.create.side_effect = _create
    tailoring_repo.get_by_id.side_effect = _get
    tailoring_repo.list_by_workflow.side_effect = _list
    tailoring_repo.set_decision.side_effect = _set_decision

    # Bundle into WorkflowDependencies (other fields can be plain mocks)
    return WorkflowDependencies(
        research_agent=MagicMock(),
        scoring_agent=MagicMock(),
        relevance_filter_agent=MagicMock(),
        resume_critic=MagicMock(),
        review_auditor=MagicMock(),
        career_advisor=MagicMock(),
        interview_coach=MagicMock(),
        tailoring_agent=tailoring_agent,
        fidelity_reviewer=fidelity_reviewer,
        resume_reviewer=MagicMock(),
        resume_chat=MagicMock(),
        discovery_service=MagicMock(),
        resume_parser=MagicMock(),
        report_generator=MagicMock(),
        job_repo=job_repo,
        score_repo=MagicMock(),
        advice_repo=advice_repo,
        review_repo=review_repo,
        tailoring_repo=tailoring_repo,
        resume_clinic_repo=MagicMock(),
        workflow_repo=MagicMock(),
        resume_repo=MagicMock(),
        observability=MagicMock(),
        checkpointer=MagicMock(),
    )


@pytest.fixture
def graph_with_job():
    return _make_graph_with_state(_state_with_job())


@pytest.fixture
def graph_missing_workflow():
    return _make_graph_with_state(None)


@pytest.fixture
def deps_for_graph(graph_with_job):
    return _make_deps(graph_with_job)


@pytest.fixture
def client(graph_with_job, deps_for_graph):
    app.dependency_overrides[get_graph] = lambda: graph_with_job
    app.dependency_overrides[get_deps] = lambda: deps_for_graph
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_trigger_returns_draft_with_evidence(client):
    resp = client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_id"] == WF_ID
    assert body["job_id"] == JOB_ID
    assert body["resume_id"] == RESUME_ID
    assert body["decision"] is None and body["approved"] is False

    # Draft body — every bullet must carry supporting_evidence
    draft = body["tailored"]
    assert draft is not None
    bullets = draft["summary_suggestions"] + draft["experience_bullet_suggestions"]
    assert all(b["supporting_evidence"] for b in bullets)

    # Gap bullet must be labelled gap, never rewritten as if present
    gaps = [b for b in draft["experience_bullet_suggestions"] if b["claim_type"] == "gap"]
    assert gaps, "expected at least one gap-labelled bullet"

    # Fidelity review attached
    fidelity = body["fidelity_review"]
    assert fidelity is not None
    assert fidelity["overall_fidelity_status"] in {"pass", "fail", "needs_revision"}
    assert fidelity["approval_recommendation"] in {"approve", "revise", "reject"}


def test_trigger_404_when_workflow_unknown(graph_missing_workflow):
    deps = _make_deps(graph_missing_workflow)
    app.dependency_overrides[get_graph] = lambda: graph_missing_workflow
    app.dependency_overrides[get_deps] = lambda: deps
    try:
        with TestClient(app) as c:
            resp = c.post(f"/workflows/missing-wf/jobs/{JOB_ID}/tailorings")
            assert resp.status_code == 404
            assert resp.json()["detail"]["error"] == "workflow_not_found"
    finally:
        app.dependency_overrides.clear()


def test_trigger_409_when_resume_profile_missing(graph_with_job, deps_for_graph):
    # Strip resume_profile from the workflow state
    bare_state = _state_with_job()
    bare_state["resume_profile"] = None
    graph_with_job.get_state.return_value = SimpleNamespace(
        values=bare_state, tasks=[], next=None
    )
    app.dependency_overrides[get_graph] = lambda: graph_with_job
    app.dependency_overrides[get_deps] = lambda: deps_for_graph
    try:
        with TestClient(app) as c:
            resp = c.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings")
            assert resp.status_code == 409
            assert resp.json()["detail"]["error"] == "resume_profile_missing"
    finally:
        app.dependency_overrides.clear()


def test_list_tailorings_returns_drafts_for_workflow(client):
    # Create two drafts, then list
    client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings")
    client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings")
    resp = client.get(f"/workflows/{WF_ID}/tailorings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_id"] == WF_ID
    assert len(body["tailorings"]) == 2


def test_decision_approve_flips_approved_flag(client):
    trig = client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings").json()
    tid = trig["tailoring_id"]

    resp = client.post(f"/tailorings/{tid}/decisions", json={"approval": "approve"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["approved"] is True
    assert body["decided_at"]


def test_decision_revise_keeps_approved_false(client):
    trig = client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings").json()
    tid = trig["tailoring_id"]

    resp = client.post(f"/tailorings/{tid}/decisions", json={"approval": "revise"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "revise"
    assert body["approved"] is False


def test_decision_edit_flips_approved_and_stores_edited(client):
    trig = client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings").json()
    tid = trig["tailoring_id"]

    edited_draft = {
        "summary_suggestions": [{"suggested_text": "My own wording, authored by me."}],
        "human_edited": True,
    }
    resp = client.post(
        f"/tailorings/{tid}/decisions",
        json={"approval": "edit", "edited": edited_draft},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "edit"
    assert body["approved"] is True       # edit accepts with the user's wording
    assert body["edited"] == edited_draft  # human-authored draft round-trips
    assert body["decided_at"]


def test_decision_edit_without_edited_body_422(client):
    trig = client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings").json()
    tid = trig["tailoring_id"]

    # approval == "edit" requires the edited draft; the model_validator rejects it.
    resp = client.post(f"/tailorings/{tid}/decisions", json={"approval": "edit"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "validation_error"


def test_decision_invalid_value_422(client):
    trig = client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings").json()
    tid = trig["tailoring_id"]

    resp = client.post(f"/tailorings/{tid}/decisions", json={"approval": "maybe"})
    assert resp.status_code == 422
    # Validation errors are normalised to the same {error, message, details} shape
    # that hand-raised HTTPExceptions use elsewhere — see app/api/main.py handler.
    body = resp.json()
    assert body["detail"]["error"] == "validation_error"
    assert "message" in body["detail"]
    assert isinstance(body["detail"]["details"], list) and body["detail"]["details"]


def test_decision_404_when_tailoring_unknown(client):
    resp = client.post("/tailorings/nope/decisions", json={"approval": "approve"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "tailoring_not_found"


def test_get_tailoring_round_trip(client):
    trig = client.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings").json()
    tid = trig["tailoring_id"]

    resp = client.get(f"/tailorings/{tid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tailoring_id"] == tid
    assert body["tailored"] is not None


# ── ADR-061: deep-review-on-demand + on-demand interview prep ─────────────────

def _configure_review_agents(deps, *, audit_score: int = 88) -> None:
    """Wire deps.resume_critic / review_auditor to return usable mock outputs so
    review_one_job runs a single round and stops (stop_recommendation=True)."""
    critic = MagicMock()
    review_obj = MagicMock()
    review_obj.model_dump.return_value = {"job_id": JOB_ID, "fit_summary": "solid"}
    critic.run.return_value = review_obj
    deps.resume_critic = critic

    auditor = MagicMock()
    audit_obj = MagicMock()
    audit_obj.model_dump.return_value = {"audit_score": audit_score}
    audit_obj.audit_score = audit_score
    audit_obj.stop_reason = "quality_met"
    audit_obj.stop_recommendation = True
    audit_obj.recommended_revision_instructions = []
    auditor.run.return_value = audit_obj
    deps.review_auditor = auditor


def test_deep_review_on_demand_returns_review(graph_with_job, deps_for_graph):
    _configure_review_agents(deps_for_graph)
    app.dependency_overrides[get_graph] = lambda: graph_with_job
    app.dependency_overrides[get_deps] = lambda: deps_for_graph
    try:
        with TestClient(app) as c:
            resp = c.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/deep-review")
            assert resp.status_code == 200
            body = resp.json()
            assert body["job_id"] == JOB_ID
            assert body["review"] == {"job_id": JOB_ID, "fit_summary": "solid"}
            assert body["rounds"] == 1
            deps_for_graph.resume_critic.run.assert_called_once()
            deps_for_graph.review_auditor.run.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_tailoring_runs_deep_review_when_no_review(graph_with_job, deps_for_graph):
    # No existing review -> ADR-061 deep-review-on-demand should fire before tailoring.
    deps_for_graph.review_repo.get_review_by_run_job.return_value = None
    _configure_review_agents(deps_for_graph)
    app.dependency_overrides[get_graph] = lambda: graph_with_job
    app.dependency_overrides[get_deps] = lambda: deps_for_graph
    try:
        with TestClient(app) as c:
            resp = c.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings")
            assert resp.status_code == 200
            deps_for_graph.resume_critic.run.assert_called_once()
            # Tailoring still produced a draft
            assert resp.json()["tailored"] is not None
    finally:
        app.dependency_overrides.clear()


def test_tailoring_skips_deep_review_when_auto_false(graph_with_job, deps_for_graph):
    deps_for_graph.review_repo.get_review_by_run_job.return_value = None
    _configure_review_agents(deps_for_graph)
    app.dependency_overrides[get_graph] = lambda: graph_with_job
    app.dependency_overrides[get_deps] = lambda: deps_for_graph
    try:
        with TestClient(app) as c:
            resp = c.post(
                f"/workflows/{WF_ID}/jobs/{JOB_ID}/tailorings",
                params={"auto_deep_review": "false"},
            )
            assert resp.status_code == 200
            deps_for_graph.resume_critic.run.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_interview_prep_on_demand_returns_prep(graph_with_job, deps_for_graph):
    coach = MagicMock()
    prep_obj = MagicMock()
    prep_obj.model_dump.return_value = {"job_id": JOB_ID, "likely_topics": ["scaling"]}
    coach.run.return_value = prep_obj
    deps_for_graph.interview_coach = coach
    app.dependency_overrides[get_graph] = lambda: graph_with_job
    app.dependency_overrides[get_deps] = lambda: deps_for_graph
    try:
        with TestClient(app) as c:
            resp = c.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/interview-prep")
            assert resp.status_code == 200
            body = resp.json()
            assert body["job_id"] == JOB_ID
            assert body["prep"] == {"job_id": JOB_ID, "likely_topics": ["scaling"]}
            deps_for_graph.interview_coach.run.assert_called_once()
            deps_for_graph.advice_repo.create_prep.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_interview_prep_409_when_resume_profile_missing(graph_with_job, deps_for_graph):
    bare_state = _state_with_job()
    bare_state["resume_profile"] = None
    graph_with_job.get_state.return_value = SimpleNamespace(
        values=bare_state, tasks=[], next=None
    )
    app.dependency_overrides[get_graph] = lambda: graph_with_job
    app.dependency_overrides[get_deps] = lambda: deps_for_graph
    try:
        with TestClient(app) as c:
            resp = c.post(f"/workflows/{WF_ID}/jobs/{JOB_ID}/interview-prep")
            assert resp.status_code == 409
            assert resp.json()["detail"]["error"] == "resume_profile_missing"
    finally:
        app.dependency_overrides.clear()
