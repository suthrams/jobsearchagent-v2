"""FastAPI dependency providers — graph singleton, real agents (Phase 7) and mocked fallback.

Phase 7 gate: if ANTHROPIC_API_KEY is set, _build_real_deps() wires ClaudeProvider,
all 8 live agents, real scrapers, and SqliteSaver. Otherwise _build_mocked_deps()
is used (all agents mocked — same as Phase 6, tests still pass).
"""
from __future__ import annotations

import logging
import os
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
from app.repositories.database import DEFAULT_DB_PATH, utcnow_iso
from app.repositories.decision_repository import DecisionRepository
from app.repositories.job_repository import JobRepository
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.resume_repository import ResumeRepository  # noqa: F401 (used in _build_mocked_deps + real)
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.security_repository import SecurityRepository
from app.repositories.step_repository import StepRepository
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
from app.services.config_service import ConfigService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.observability_service import ObservabilityService
from app.services.report_generator import ReportGenerator
from app.services.resume_parser import ResumeParser
from app.workflows.workflow_graph import WorkflowDependencies, build_graph

logger = logging.getLogger(__name__)

_graph = None
_cleanup_fn = None  # called by cleanup_graph() in lifespan teardown


# ── mock side-effects (Phase 6 / test mode) ──────────────────────────────────

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


# ── mocked deps (Phase 6 / no ANTHROPIC_API_KEY) ─────────────────────────────

def _mock_resume_repo() -> MagicMock:
    """Resume repo mock that returns None from get_by_id, keeping tests on the parse_pdf path."""
    m = MagicMock(spec=ResumeRepository)
    m.get_by_id.return_value = None
    return m


def _build_mocked_deps(checkpointer) -> WorkflowDependencies:
    """WorkflowDependencies with all 8 agents mocked via side_effect."""
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
        resume_repo=_mock_resume_repo(),
        observability=obs,
        checkpointer=checkpointer,
    )


# ── real deps (Phase 7 / ANTHROPIC_API_KEY set) ───────────────────────────────

def _build_real_deps(checkpointer) -> WorkflowDependencies:
    """Build WorkflowDependencies wired to real Claude agents and SQLite repos."""
    from pathlib import Path

    from app.providers.claude_provider import ClaudeProvider, make_resume_enhance_fn
    from app.providers.model_registry import ModelRegistry, assignment_from_config
    from app.providers.prompt_loader import PromptLoader

    # Anchor all paths to the project root (two levels up from app/api/dependencies.py)
    # so this works regardless of CWD (server, notebook, or test runner).
    _project_root = Path(__file__).resolve().parents[2]
    db_path = _project_root / "data" / "v2.db"

    # Ensure schema exists (CREATE TABLE IF NOT EXISTS — safe to call repeatedly)
    from app.repositories.database import init_db
    init_db(db_path)

    # Repositories — each manages its own connection via get_connection()
    job_repo = JobRepository(db_path)
    score_repo = ScoreRepository(db_path)
    advice_repo = AdviceRepository(db_path)
    review_repo = ReviewRepository(db_path)
    tailoring_repo = TailoringRepository(db_path)
    workflow_repo = WorkflowRepository(db_path)
    resume_repo = ResumeRepository(db_path)
    obs_repo = ObservabilityRepository(db_path)
    step_repo = StepRepository(db_path)
    decision_repo = DecisionRepository(db_path)
    security_repo = SecurityRepository(db_path)
    report_repo = ReportRepository(db_path)

    # ConfigService — load early so the agent assignment can come from user overrides
    loader = PromptLoader()
    config_svc_for_models = ConfigService(
        config_path=_project_root / "config" / "config.yaml",
        db_path=db_path,
    )
    _eff = config_svc_for_models.get_effective_config()

    # Per-agent provider/model assignment (ADR-053). Defaults from ModelRegistry,
    # overrides from effective_config.agents.*.
    agent_assignment = assignment_from_config(_eff)
    registry = ModelRegistry.build(loader, agent_assignment)
    logger.info("ModelRegistry built; agent assignment: %s", registry.assignment())

    # Observability service
    obs = ObservabilityService(obs_repo, step_repo, decision_repo, security_repo)

    # Agents — each gets the provider its assignment maps to
    research = ResearchAgent(registry.for_agent("research_agent"), obs)
    scoring = ScoringAgent(registry.for_agent("scoring_agent"), obs)
    critic = ResumeCritic(registry.for_agent("resume_critic"), obs)
    auditor = ReviewAuditor(registry.for_agent("review_auditor"), obs)
    advisor = CareerAdvisor(registry.for_agent("career_advisor"), obs)
    coach = InterviewCoach(registry.for_agent("interview_coach"), obs)
    tailoring = TailoringAgent(registry.for_agent("tailoring_agent"), obs)
    fidelity = FidelityReviewer(registry.for_agent("fidelity_reviewer"), obs)

    # ResumeParser uses its own assigned provider too
    enhance_fn = make_resume_enhance_fn(registry.for_agent("resume_parser"))
    resume_parser = ResumeParser(resume_repo, enhance_fn=enhance_fn)

    # ConfigService for the rest of the build (already loaded above for the registry)
    config_svc = config_svc_for_models
    config_dict = _eff

    # JobDiscoveryService with real scrapers (only include scrapers whose creds are present)
    scrapers = _build_scrapers(config_dict)
    discovery_svc = JobDiscoveryService(job_repo, config_dict, scrapers=scrapers)

    # ReportGenerator
    report_gen = ReportGenerator(score_repo, review_repo, advice_repo, tailoring_repo, report_repo, job_repo)

    # Custom URL scraper factory — built per workflow run with the user-supplied URLs.
    from app.services.custom_url_scraper import CustomUrlScraper
    _custom_url_provider = registry.for_agent("custom_url_extractor")
    custom_url_factory = lambda urls: CustomUrlScraper(urls, llm_client=_custom_url_provider)

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
        job_repo=job_repo,
        score_repo=score_repo,
        advice_repo=advice_repo,
        review_repo=review_repo,
        tailoring_repo=tailoring_repo,
        workflow_repo=workflow_repo,
        resume_repo=resume_repo,
        observability=obs,
        checkpointer=checkpointer,
        custom_url_scraper_factory=custom_url_factory,
    )


def _build_scrapers(config_dict: dict) -> list:
    """Instantiate v1 scrapers that have their required credentials present."""
    scrapers = []

    # LinkedIn: requires a populated inbox file (one URL per line)
    try:
        from pathlib import Path as _Path
        from scrapers.linkedin import LinkedInScraper
        _root = _Path(__file__).resolve().parents[2]
        inbox = _root / "data" / "linkedin_inbox.txt"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        if not inbox.exists():
            inbox.write_text("")
        scrapers.append(LinkedInScraper(str(inbox)))
        logger.info("LinkedInScraper registered (inbox: %s)", inbox)
    except Exception as exc:
        logger.warning("LinkedInScraper skipped: %s", exc)

    # Adzuna: requires ADZUNA_APP_ID + ADZUNA_APP_KEY
    if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"):
        try:
            from models.config_schema import AdzunaConfig
            from app.services.concurrent_adzuna_scraper import ConcurrentAdzunaScraper
            adzuna_raw = config_dict.get("scrapers", {}).get("adzuna", {})
            adzuna_cfg = AdzunaConfig(**adzuna_raw)
            titles = config_dict.get("search", {}).get("titles", [])
            scraper = ConcurrentAdzunaScraper.make(adzuna_cfg, titles)
            if scraper:
                scrapers.append(scraper)
                logger.info("ConcurrentAdzunaScraper registered (%d titles, 5 workers)", len(titles))
        except Exception as exc:
            logger.warning("AdzunaScraper skipped: %s", exc)
    else:
        logger.info("AdzunaScraper skipped: ADZUNA_APP_ID/ADZUNA_APP_KEY not set")

    return scrapers


# ── startup / teardown ────────────────────────────────────────────────────────

def build_and_cache_graph() -> None:
    """Build the workflow graph once at startup; store graph and cleanup fn in module singletons.

    Phase 7 gate: ANTHROPIC_API_KEY present → real agents + SqliteSaver.
                  Not set → mocked agents + MemorySaver (Phase 6 behaviour, tests pass).
    """
    global _graph, _cleanup_fn

    if os.getenv("ANTHROPIC_API_KEY"):
        logger.info("ANTHROPIC_API_KEY detected — starting in live-agent mode (Phase 7)")
        from pathlib import Path as _Path
        from langgraph.checkpoint.sqlite import SqliteSaver
        _db = _Path(__file__).resolve().parents[2] / "data" / "v2.db"
        checkpointer_cm = SqliteSaver.from_conn_string(str(_db))
        checkpointer = checkpointer_cm.__enter__()
        deps = _build_real_deps(checkpointer)
        _cleanup_fn = lambda: checkpointer_cm.__exit__(None, None, None)
    else:
        logger.info("ANTHROPIC_API_KEY not set — starting in mock mode (Phase 6)")
        from langgraph.checkpoint.memory import MemorySaver
        deps = _build_mocked_deps(MemorySaver())
        _cleanup_fn = None

    _graph = build_graph(deps)
    logger.info("Workflow graph built and cached.")


def cleanup_graph() -> None:
    """Release SqliteSaver and any other resources opened at startup."""
    global _cleanup_fn
    if _cleanup_fn:
        _cleanup_fn()
        _cleanup_fn = None


def get_graph():
    """FastAPI dependency that returns the cached graph. Raises RuntimeError if not built."""
    if _graph is None:
        raise RuntimeError(
            "Workflow graph not initialised. build_and_cache_graph() must be called at startup."
        )
    return _graph
