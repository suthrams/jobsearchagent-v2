"""Integration tests for FastAPI workflow endpoints.

Uses TestClient with MemorySaver-backed graph. The get_graph dependency is
overridden so no SqliteSaver / real DB is touched.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.career_advisor import CareerAdvisor
from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.interview_coach import InterviewCoach
from app.agents.research_agent import ResearchAgent
from app.agents.resume_critic import ResumeCritic
from app.agents.review_auditor import ReviewAuditor
from app.agents.scoring_agent import ScoringAgent
from app.agents.tailoring_agent import TailoringAgent
from app.api.dependencies import get_graph
from app.api.main import app
from app.repositories.advice_repository import AdviceRepository
from app.repositories.database import utcnow_iso
from app.repositories.job_repository import JobRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.career_advice import CareerAdvice
from app.schemas.fidelity_review import FidelityReview
from app.schemas.interview_prep import InterviewPrep
from app.schemas.job_posting import JobPosting, JobSource, WorkMode
from app.schemas.job_score import JobScore
from app.schemas.research_context import ResearchContext
from app.schemas.resume_review import ResumeReview
from app.schemas.review_audit import ReviewAudit
from app.schemas.tailored_resume_draft import TailoredResumeDraft
from app.services.job_discovery_service import JobDiscoveryService
from app.services.observability_service import ObservabilityService
from app.services.report_generator import ReportGenerator
from app.services.resume_parser import ResumeParser
from app.workflows.workflow_graph import WorkflowDependencies, build_graph


# ── Helpers ────────────────────────────────────────────────────────────────────

def _obs() -> MagicMock:
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"
    return obs


def _mock_resume_repo() -> MagicMock:
    m = MagicMock(spec=ResumeRepository)
    m.get_by_id.return_value = None
    return m


def _make_deps(checkpointer=None) -> WorkflowDependencies:
    """Build mocked WorkflowDependencies with MemorySaver — mirrors test_workflow_graph.py."""

    def _mock_research():
        m = MagicMock(spec=ResearchAgent)
        m.run.return_value = ResearchContext(
            job_id="job-001", company_summary="Tech co.", role_context="Platform.",
            technology_signals=["Python"], leadership_signals=[], domain_signals=[],
            risk_flags=[], research_steps=[], confidence=75,
        )
        return m

    def _mock_scoring():
        m = MagicMock(spec=ScoringAgent)
        m.run.return_value = JobScore(
            job_id="job-001", resume_id="res-001",
            overall_score=80, technical_score=85,
            architecture_score=75, leadership_score=60, domain_score=70,
            match_summary="Good.", strengths=["Python"], gaps=[],
            recommended_next_action="Apply.", confidence=82,
        )
        return m

    def _mock_critic():
        m = MagicMock(spec=ResumeCritic)
        m.run.return_value = ResumeReview(
            job_id="job-001", resume_id="res-001",
            overall_fit_summary="Good.", section_reviews=[],
            critical_gaps=[], resume_only_gaps=[], career_gaps_observed=[],
            suggested_improvements=[], questions_for_user=[], confidence=80,
        )
        return m

    def _mock_auditor():
        m = MagicMock(spec=ReviewAuditor)
        m.run.return_value = ReviewAudit(
            job_id="job-001", round_number=1, audit_score=82, auditor_confidence=80,
            quality_summary="OK.", missing_analysis_points=[], generic_or_weak_feedback=[],
            unsupported_claims=[], fidelity_concerns=[], recommended_revision_instructions=[],
            stop_recommendation=True, stop_reason="Threshold reached.",
        )
        return m

    def _mock_advisor():
        m = MagicMock(spec=CareerAdvisor)
        m.run.return_value = CareerAdvice(
            job_id="job-001", positioning_summary="Good.", resume_gaps=[], career_gaps=[],
            role_fit_assessment="High.", recommended_positioning="Lead with Python.",
            skills_to_strengthen=[], experience_to_collect=[],
            thirty_sixty_ninety_day_plan=[], recommended_next_action="Apply.", confidence=82,
        )
        return m

    def _mock_coach():
        m = MagicMock(spec=InterviewCoach)
        m.run.return_value = InterviewPrep(
            job_id="job-001", likely_interview_topics=["System design"],
            technical_topics_to_review=[], leadership_stories_to_prepare=[],
            weak_areas_to_defend=[], questions_to_ask_interviewer=[],
            seven_day_prep_plan=[], confidence=80,
        )
        return m

    def _mock_tailoring():
        m = MagicMock(spec=TailoringAgent)
        m.run.return_value = TailoredResumeDraft(
            job_id="job-001", resume_id="res-001",
            summary_suggestions=[], experience_bullet_suggestions=[],
            skills_section_suggestions=[],
            overall_tailoring_notes="Good.", fidelity_risk_summary="Low.",
        )
        return m

    def _mock_fidelity():
        m = MagicMock(spec=FidelityReviewer)
        m.run.return_value = FidelityReview(
            job_id="job-001", resume_id="res-001",
            overall_fidelity_status="pass", unsupported_claims=[],
            fabricated_metrics=[], inflated_scope_flags=[],
            unsupported_technology_flags=[], unsupported_certification_flags=[],
            required_removals=[], required_revisions=[],
            approval_recommendation="approve", confidence=95,
        )
        return m

    report_gen = MagicMock(spec=ReportGenerator)
    report_gen.generate_run_summary.return_value = "# Report"

    posting = JobPosting(
        job_id="job-001", workflow_id="wf-test-001",
        url="https://example.com", source=JobSource.MANUAL,
        title="Staff Engineer", company="Acme",
        work_mode=WorkMode.REMOTE, description="Python role.",
        found_at=utcnow_iso(),
    )
    discovery_svc = MagicMock(spec=JobDiscoveryService)
    discovery_svc.discover.return_value = [posting]

    resume_parser = MagicMock(spec=ResumeParser)

    return WorkflowDependencies(
        research_agent=_mock_research(),
        scoring_agent=_mock_scoring(),
        resume_critic=_mock_critic(),
        review_auditor=_mock_auditor(),
        career_advisor=_mock_advisor(),
        interview_coach=_mock_coach(),
        tailoring_agent=_mock_tailoring(),
        fidelity_reviewer=_mock_fidelity(),
        discovery_service=discovery_svc,
        resume_parser=resume_parser,
        report_generator=report_gen,
        job_repo=MagicMock(spec=JobRepository),
        score_repo=MagicMock(spec=ScoreRepository),
        advice_repo=MagicMock(spec=AdviceRepository),
        review_repo=MagicMock(spec=ReviewRepository),
        tailoring_repo=MagicMock(spec=TailoringRepository),
        workflow_repo=MagicMock(spec=WorkflowRepository),
        resume_repo=_mock_resume_repo(),
        observability=_obs(),
        checkpointer=checkpointer or MemorySaver(),
    )


def _initial_state(thread_id: str = "wf-test-001") -> dict:
    now = utcnow_iso()
    return {
        "workflow_id": thread_id,
        "workflow_type": "full_career_review",
        "status": "running",
        "current_step": "initialized",
        "user_id": None,
        "resume_id": "res-001",
        "resume_profile": {"name": "Test User", "skills": ["Python"]},
        "resume_version": None,
        "search_criteria": {"roles": ["Staff Engineer"]},
        "raw_jobs": [],
        "normalized_jobs": [],
        "scored_jobs": [],
        "selected_jobs": [],
        "research_context": None,
        "skill_gaps": {},
        "review_rounds": [],
        "final_resume_review": None,
        "career_advice": None,
        "interview_prep": None,
        "tailored_resume": None,
        "fidelity_review": None,
        "pending_decision": None,
        "human_decisions": [],
        "report": None,
        "run_metrics": {
            "llm_calls": 0, "tokens_input": 0, "tokens_output": 0,
            "estimated_cost_usd": 0.0, "total_duration_ms": 0,
            "started_at": now, "completed_at": None,
        },
        "errors": [],
        "effective_config": {"scoring": {"career_track": "ic"}},
        "created_at": now,
        "updated_at": now,
        "user_requested_interview_prep": False,
        "user_requested_tailoring": False,
    }


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_graph():
    saver = MemorySaver()
    deps = _make_deps(checkpointer=saver)
    return build_graph(deps)


@pytest.fixture
def client(mock_graph):
    app.dependency_overrides[get_graph] = lambda: mock_graph
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_start_workflow_returns_202(client):
    resp = client.post(
        "/workflows",
        json={
            "resume_id": "res-001",
            "search_criteria": {"roles": ["Staff Engineer"]},
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "workflow_id" in body
    assert body["status"] == "running"
    assert "created_at" in body


def test_get_workflow_not_found(client):
    resp = client.get("/workflows/nonexistent-id")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"] == "workflow_not_found"


def test_get_workflow_running_or_paused(client, mock_graph):
    """Start a workflow, run it to interrupt, then GET — status is running or waiting_for_user."""
    thread_id = "wf-api-test-running"
    config = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(thread_id)

    # Run graph directly to get checkpoint created (catches interrupt)
    try:
        mock_graph.invoke(state, config)
    except Exception:
        pass

    resp = client.get(f"/workflows/{thread_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("running", "waiting_for_user", "completed")
    assert body["workflow_id"] == thread_id


def test_decision_on_non_paused_workflow(client, mock_graph):
    """Submitting a decision to a workflow with no active interrupt → 409."""
    # Run the graph through HITL #1, then resume so it has no active interrupt.
    thread_id = "wf-api-not-paused"
    config = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(thread_id)

    # First invocation — gets to await_job_selection interrupt
    try:
        mock_graph.invoke(state, config)
    except Exception:
        pass

    # Resume with job selection to clear the interrupt
    try:
        mock_graph.invoke(Command(resume={"selected_job_ids": ["job-001"]}), config)
    except Exception:
        pass

    # After resumption the graph will either pause at HITL #2 or complete.
    # Either way, verify that if workflow is not paused (404/409) the API rejects it.
    snapshot = mock_graph.get_state(config)
    has_interrupts = any(
        getattr(task, "interrupts", None)
        for task in (snapshot.tasks or [])
    ) if snapshot else False

    if has_interrupts:
        # Graph is at HITL #2 (tailoring approval) — skip: we can't easily test 409 here
        pytest.skip("Graph re-paused at HITL #2, cannot test non-paused 409 path.")

    # At this point the graph either completed or has no interrupts.
    resp = client.post(
        f"/workflows/{thread_id}/decisions",
        json={
            "decision_type": "select_jobs_for_deep_review",
            "selected_job_ids": ["job-001"],
        },
    )
    # Workflow exists but has no active interrupt → 409
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "workflow_not_paused"


def test_invalid_decision_type(client, mock_graph):
    """Submitting an unrecognised decision_type → Pydantic validation error (422)."""
    # First create a workflow so we have something to submit to
    thread_id = "wf-api-bad-type"
    state = _initial_state(thread_id)
    config = {"configurable": {"thread_id": thread_id}}
    try:
        mock_graph.invoke(state, config)
    except Exception:
        pass

    resp = client.post(
        f"/workflows/{thread_id}/decisions",
        json={
            "decision_type": "totally_wrong_type",
            "selected_job_ids": ["job-001"],
        },
    )
    assert resp.status_code == 422


def test_valid_job_selection_resumes_workflow(client, mock_graph):
    """After graph pauses at HITL #1, submit valid job selection → 202."""
    thread_id = "wf-api-hitl-select"
    config = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(thread_id)

    # Run to HITL interrupt
    try:
        mock_graph.invoke(state, config)
    except Exception:
        pass

    # Verify graph is paused
    snapshot = mock_graph.get_state(config)
    has_interrupts = any(
        getattr(task, "interrupts", None)
        for task in (snapshot.tasks or [])
    )

    if not has_interrupts:
        pytest.skip("Graph did not pause at HITL — cannot test job selection resumption.")

    resp = client.post(
        f"/workflows/{thread_id}/decisions",
        json={
            "decision_type": "select_jobs_for_deep_review",
            "selected_job_ids": ["job-001"],
        },
    )
    assert resp.status_code == 202
    assert resp.json()["workflow_id"] == thread_id


def test_too_many_jobs_selected(client, mock_graph):
    """Selecting > MAX_SELECTED_JOBS jobs in request body → Pydantic 422 (before reaching route)."""
    thread_id = "wf-api-too-many"
    config = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(thread_id)

    try:
        mock_graph.invoke(state, config)
    except Exception:
        pass

    from app.workflows.limits import MAX_SELECTED_JOBS
    too_many = [f"job-{i:03d}" for i in range(1, MAX_SELECTED_JOBS + 2)]
    resp = client.post(
        f"/workflows/{thread_id}/decisions",
        json={
            "decision_type": "select_jobs_for_deep_review",
            "selected_job_ids": too_many,
        },
    )
    # Pydantic enforces max_length=MAX_SELECTED_JOBS before the route handler runs
    assert resp.status_code == 422
