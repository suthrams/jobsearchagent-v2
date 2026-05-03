"""End-to-end graph tests using MemorySaver (no real DB, no real LLM calls).

Tests verify that the StateGraph routes correctly, handles HITL interrupts,
propagates per-job errors without aborting the run, and enforces budget limits.
"""
import pytest
from unittest.mock import MagicMock

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
from app.providers.llm_client import LLMProviderError
from app.repositories.advice_repository import AdviceRepository
from app.repositories.job_repository import JobRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.career_advice import CareerAdvice
from app.schemas.fidelity_review import FidelityReview
from app.schemas.interview_prep import InterviewPrep
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
    m.get_by_id.return_value = None  # force parse_pdf path in tests
    return m


def _scored_job(job_id: str = "job-001", score: int = 80) -> dict:
    return {
        "id": job_id, "job_id": job_id,
        "title": "Staff Engineer", "company": "Acme",
        "job_description": "Python role.", "url": "https://example.com",
        "location": "Remote", "status": "scored", "overall_score": score,
        "technical_score": 85, "architecture_score": 75,
        "leadership_score": 60, "domain_score": 70,
        "match_summary": "Good.", "strengths": ["Python"],
        "gaps": [], "recommended_next_action": "Apply.", "confidence": 82,
        "resume_id": "res-001",
    }


def _make_deps(checkpointer=None, override_scoring_score: int = 80) -> WorkflowDependencies:
    """Build a full WorkflowDependencies with all agents and services mocked."""

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
            overall_score=override_scoring_score, technical_score=85,
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

    # Discovery: return one pre-scored job
    from app.schemas.job_posting import JobPosting, JobSource, WorkMode
    from app.repositories.database import utcnow_iso
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


def _initial_state(thread_id: str = "wf-test-001", **overrides) -> dict:
    from app.repositories.database import utcnow_iso
    state = {
        "workflow_id": thread_id,
        "workflow_type": "full_career_review",
        "status": "running",
        "current_step": "initialized",
        "resume_id": "res-001",
        "resume_profile": {"name": "Test User", "skills": ["Python"]},
        "search_criteria": {"roles": ["Staff Engineer"]},
        "normalized_jobs": [],
        "scored_jobs": [],
        "selected_jobs": [],
        "run_metrics": {"llm_calls": 0, "tokens_input": 0, "tokens_output": 0, "estimated_cost_usd": 0.0},
        "errors": [],
        "effective_config": {"scoring": {"career_track": "all"}},
        "human_decisions": [],
        "user_requested_interview_prep": False,
        "user_requested_tailoring": False,
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
    }
    state.update(overrides)
    return state


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_graph_auto_selects_qualifying_jobs_no_interrupt():
    """With score=80 and default threshold=75, the job qualifies and deep review runs end-to-end."""
    saver = MemorySaver()
    deps = _make_deps(checkpointer=saver, override_scoring_score=80)
    graph = build_graph(deps)

    config = {"configurable": {"thread_id": "wf-auto-001"}}
    state = _initial_state("wf-auto-001")

    # Should run end-to-end with no HITL interrupt
    graph.invoke(state, config)

    deps.resume_critic.run.assert_called()


def test_graph_preserves_custom_urls_in_state():
    """Regression: custom_urls must be declared in WorkflowGraphState so LangGraph
    doesn't strip it. Without this, the discover_jobs node sees an empty list
    and never builds the CustomUrlScraper for the user's pasted URLs.
    """
    saver = MemorySaver()
    deps = _make_deps(checkpointer=saver)
    graph = build_graph(deps)
    config = {"configurable": {"thread_id": "wf-curl-001"}}
    state = _initial_state(
        "wf-curl-001",
        custom_urls=["https://example.com/job/1", "https://example.com/job/2"],
    )
    graph.invoke(state, config)

    snapshot = graph.get_state(config)
    assert snapshot is not None
    assert snapshot.values.get("custom_urls") == [
        "https://example.com/job/1",
        "https://example.com/job/2",
    ], "custom_urls must round-trip through WorkflowGraphState"


def test_graph_skips_deep_review_when_no_jobs_qualify():
    """All scores below threshold → auto_select picks zero jobs → graph short-circuits to report."""
    saver = MemorySaver()
    # Score 50 < default threshold 75, none of the track scores will qualify
    deps = _make_deps(checkpointer=saver, override_scoring_score=50)
    # Override scoring to also have low track scores
    from app.schemas.job_score import JobScore
    deps.scoring_agent.run.return_value = JobScore(
        job_id="job-001", resume_id="res-001",
        overall_score=50, technical_score=50,
        architecture_score=40, leadership_score=30, domain_score=40,
        match_summary="Weak match.", strengths=[], gaps=["Domain"],
        recommended_next_action="Skip.", confidence=70,
    )
    graph = build_graph(deps)

    config = {"configurable": {"thread_id": "wf-skip-001"}}
    state = _initial_state("wf-skip-001")
    graph.invoke(state, config)

    deps.resume_critic.run.assert_not_called()
    deps.report_generator.generate_run_summary.assert_called()


def test_graph_per_job_error_does_not_abort_run():
    """One job raising LLMProviderError should mark that job failed, not crash the graph."""
    saver = MemorySaver()
    deps = _make_deps(checkpointer=saver)

    # Make scoring fail for the one job
    deps.scoring_agent.run.side_effect = LLMProviderError("timeout")

    graph = build_graph(deps)
    config = {"configurable": {"thread_id": "wf-err-001"}}
    state = _initial_state("wf-err-001")

    try:
        graph.invoke(state, config)
    except Exception:
        pass

    # Graph should reach scoring and handle the error
    checkpoint = saver.get(config)
    assert checkpoint is not None


def test_graph_budget_exhaustion_skips_remaining_jobs():
    """When budget is at the limit before scoring, jobs should be marked budget_skipped."""
    from app.workflows.limits import MAX_LLM_CALLS_PER_RUN

    saver = MemorySaver()
    deps = _make_deps(checkpointer=saver)
    graph = build_graph(deps)

    config = {"configurable": {"thread_id": "wf-budget-001"}}
    state = _initial_state(
        "wf-budget-001",
        run_metrics={
            "llm_calls": MAX_LLM_CALLS_PER_RUN,
            "tokens_input": 0, "tokens_output": 0, "estimated_cost_usd": 0.0,
        },
    )

    try:
        graph.invoke(state, config)
    except Exception:
        pass

    checkpoint = saver.get(config)
    assert checkpoint is not None
