"""FastAPI dependency providers — graph singleton and mocked agents for Phase 6.

Phase 6 uses mocked agents (Phase 7 replaces with real ones). All agents are set up
with side_effect so they return correct Pydantic schema instances based on job_id
from the context passed to agent.run().
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from app.agents.career_advisor import CareerAdvisor
from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.interview_coach import InterviewCoach
from app.agents.research_agent import ResearchAgent
from app.agents.resume_critic import ResumeCritic
from app.agents.review_auditor import ReviewAuditor
from app.agents.scoring_agent import ScoringAgent
from app.agents.tailoring_agent import TailoringAgent
from app.repositories.advice_repository import AdviceRepository
from app.repositories.database import utcnow_iso
from app.repositories.job_repository import JobRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.tailoring_repository import TailoringRepository
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

logger = logging.getLogger(__name__)

_graph = None


def _make_research_side_effect(workflow_id: str, context: dict) -> ResearchContext:
    job_id = context.get("job_id", "job-unknown")
    return ResearchContext(
        job_id=job_id,
        company_summary="Tech co.",
        role_context="Platform role.",
        technology_signals=["Python"],
        leadership_signals=[],
        domain_signals=[],
        risk_flags=[],
        research_steps=[],
        confidence=75,
    )


def _make_scoring_side_effect(workflow_id: str, context: dict) -> JobScore:
    job_id = context.get("job_id", "job-unknown")
    resume_id = context.get("resume_id", "res-unknown")
    return JobScore(
        job_id=job_id,
        resume_id=resume_id,
        overall_score=80,
        technical_score=85,
        architecture_score=75,
        leadership_score=60,
        domain_score=70,
        match_summary="Good match.",
        strengths=["Python", "System design"],
        gaps=[],
        recommended_next_action="Apply.",
        confidence=82,
    )


def _make_critic_side_effect(workflow_id: str, context: dict) -> ResumeReview:
    job_id = context.get("job_id", "job-unknown")
    resume_id = context.get("resume_id", "res-unknown")
    return ResumeReview(
        job_id=job_id,
        resume_id=resume_id,
        overall_fit_summary="Good fit.",
        section_reviews=[],
        critical_gaps=[],
        resume_only_gaps=[],
        career_gaps_observed=[],
        suggested_improvements=[],
        questions_for_user=[],
        confidence=80,
    )


def _make_auditor_side_effect(workflow_id: str, context: dict) -> ReviewAudit:
    job_id = context.get("job_id", "job-unknown")
    return ReviewAudit(
        job_id=job_id,
        round_number=1,
        audit_score=82,
        auditor_confidence=80,
        quality_summary="Quality is acceptable.",
        missing_analysis_points=[],
        generic_or_weak_feedback=[],
        unsupported_claims=[],
        fidelity_concerns=[],
        recommended_revision_instructions=[],
        stop_recommendation=True,
        stop_reason="Quality threshold reached.",
    )


def _make_advisor_side_effect(workflow_id: str, context: dict) -> CareerAdvice:
    job_id = context.get("job_id", "job-unknown")
    return CareerAdvice(
        job_id=job_id,
        positioning_summary="Strong candidate.",
        resume_gaps=[],
        career_gaps=[],
        role_fit_assessment="High fit.",
        recommended_positioning="Lead with Python experience.",
        skills_to_strengthen=[],
        experience_to_collect=[],
        thirty_sixty_ninety_day_plan=[],
        recommended_next_action="Apply.",
        confidence=82,
    )


def _make_coach_side_effect(workflow_id: str, context: dict) -> InterviewPrep:
    job_id = context.get("job_id", "job-unknown")
    return InterviewPrep(
        job_id=job_id,
        likely_interview_topics=["System design", "Python"],
        technical_topics_to_review=[],
        leadership_stories_to_prepare=[],
        weak_areas_to_defend=[],
        questions_to_ask_interviewer=[],
        seven_day_prep_plan=[],
        confidence=80,
    )


def _make_tailoring_side_effect(workflow_id: str, context: dict) -> TailoredResumeDraft:
    job_id = context.get("job_id", "job-unknown")
    resume_id = context.get("resume_id", "res-unknown")
    return TailoredResumeDraft(
        job_id=job_id,
        resume_id=resume_id,
        summary_suggestions=[],
        experience_bullet_suggestions=[],
        skills_section_suggestions=[],
        overall_tailoring_notes="Tailoring complete.",
        fidelity_risk_summary="Low risk.",
    )


def _make_fidelity_side_effect(workflow_id: str, context: dict) -> FidelityReview:
    job_id = context.get("job_id", "job-unknown")
    resume_id = context.get("resume_id", "res-unknown")
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


def _build_mocked_deps(checkpointer) -> WorkflowDependencies:
    """Build WorkflowDependencies with all 8 agents mocked via side_effect."""
    obs = MagicMock(spec=ObservabilityService)
    obs.log_agent_started.return_value = "evt-001"

    research = MagicMock(spec=ResearchAgent)
    research.run.side_effect = _make_research_side_effect

    scoring = MagicMock(spec=ScoringAgent)
    scoring.run.side_effect = _make_scoring_side_effect

    critic = MagicMock(spec=ResumeCritic)
    critic.run.side_effect = _make_critic_side_effect

    auditor = MagicMock(spec=ReviewAuditor)
    auditor.run.side_effect = _make_auditor_side_effect

    advisor = MagicMock(spec=CareerAdvisor)
    advisor.run.side_effect = _make_advisor_side_effect

    coach = MagicMock(spec=InterviewCoach)
    coach.run.side_effect = _make_coach_side_effect

    tailoring = MagicMock(spec=TailoringAgent)
    tailoring.run.side_effect = _make_tailoring_side_effect

    fidelity = MagicMock(spec=FidelityReviewer)
    fidelity.run.side_effect = _make_fidelity_side_effect

    posting = JobPosting(
        job_id="job-001",
        workflow_id="wf-placeholder",
        url="https://example.com",
        source=JobSource.MANUAL,
        title="Staff Engineer",
        company="Acme",
        work_mode=WorkMode.REMOTE,
        description="Python role.",
        found_at=utcnow_iso(),
    )
    discovery_svc = MagicMock(spec=JobDiscoveryService)
    discovery_svc.discover.return_value = [posting]

    resume_parser = MagicMock(spec=ResumeParser)

    report_gen = MagicMock(spec=ReportGenerator)
    report_gen.generate_run_summary.return_value = "# Report"

    return WorkflowDependencies(
        research_agent=research,
        scoring_agent=scoring,
        resume_critic=critic,
        review_auditor=auditor,
        career_advisor=advisor,
        interview_coach=coach,
        tailoring_agent=tailoring,
        fidelity_reviewer=fidelity,
        discovery_service=discovery_svc,
        resume_parser=resume_parser,
        report_generator=report_gen,
        job_repo=MagicMock(spec=JobRepository),
        score_repo=MagicMock(spec=ScoreRepository),
        advice_repo=MagicMock(spec=AdviceRepository),
        review_repo=MagicMock(spec=ReviewRepository),
        tailoring_repo=MagicMock(spec=TailoringRepository),
        workflow_repo=MagicMock(spec=WorkflowRepository),
        observability=obs,
        checkpointer=checkpointer,
    )


def build_and_cache_graph() -> None:
    """Build the workflow graph once at startup and store in module-level singleton.

    Phase 6 uses MemorySaver (all agents mocked). Phase 7 will switch to SqliteSaver.
    """
    global _graph
    from langgraph.checkpoint.memory import MemorySaver
    deps = _build_mocked_deps(MemorySaver())
    _graph = build_graph(deps)
    logger.info("Workflow graph built and cached.")


def get_graph():
    """FastAPI dependency that returns the cached graph. Raises RuntimeError if not built."""
    if _graph is None:
        raise RuntimeError(
            "Workflow graph not initialised. build_and_cache_graph() must be called at startup."
        )
    return _graph
