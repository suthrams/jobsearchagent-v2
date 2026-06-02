"""FastAPI dependency providers — graph singleton, real agents (Phase 7) and mocked fallback.

Phase 7 gate: if ANTHROPIC_API_KEY is set, _build_real_deps() wires ClaudeProvider,
all 8 live agents, real scrapers, and SqliteSaver. Otherwise _build_mocked_deps()
is used (all agents mocked — same as Phase 6, tests still pass).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock

from app.agents.career_advisor import CareerAdvisor
from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.interview_coach import InterviewCoach
from app.agents.research_agent import ResearchAgent
from app.agents.resume_chat import ResumeChatAgent
from app.agents.resume_critic import ResumeCritic
from app.agents.resume_reviewer import ResumeReviewerAgent
from app.agents.review_auditor import ReviewAuditor
from app.agents.scoring_agent import ScoringAgent
from app.agents.tailoring_agent import TailoringAgent
from app.repositories.advice_repository import AdviceRepository
from app.repositories.database import DEFAULT_USER_ID, utcnow_iso
from app.repositories.decision_repository import DecisionRepository
from app.repositories.job_repository import JobRepository
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.resume_clinic_repository import ResumeClinicRepository
from app.repositories.resume_repository import ResumeRepository  # noqa: F401 (used in _build_mocked_deps + real)
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.security_repository import SecurityRepository
from app.repositories.step_repository import StepRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.user_repository import UserRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.career_advice import CareerAdvice
from app.schemas.fidelity_review import FidelityReview
from app.schemas.interview_prep import InterviewPrep
from app.schemas.job_posting import JobPosting, JobSource, WorkMode
from app.schemas.job_score import JobScore
from app.schemas.research_context import ResearchContext
from app.schemas.resume_chat import ResumeChatTurnResult
from app.schemas.resume_clinic import ResumeClinicReview
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
_deps: WorkflowDependencies | None = None  # populated alongside _graph; routers can read individual components
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


def _make_resume_chat_side_effect(workflow_id: str, context: dict) -> ResumeChatTurnResult:
    """ADR-068: mocked chat-revise turn. Echoes the current overhaul back
    unchanged with an acknowledging reply, so the runner's persistence +
    fidelity glue can be exercised without hitting a real model."""
    current = context.get("current_overhaul") or {}
    overhaul = {
        "reorganization": current.get("reorganization") or {
            "section_order": ["summary", "experience"], "moves": [],
        },
        "rewrites": current.get("rewrites") or [],
    }
    return ResumeChatTurnResult(
        reply="Acknowledged - test mock returns the overhaul unchanged.",
        overhaul=overhaul,
        changed_sections=[],
    )


def _make_resume_reviewer_side_effect(workflow_id: str, context: dict) -> ResumeClinicReview:
    """ADR-066: mock clinic reviewer used in the unit-test path. Returns a
    minimal valid ResumeClinicReview so the runner's persistence + fidelity
    glue can be exercised without hitting a real model."""
    return ResumeClinicReview(
        quality={
            "dimensions": [
                {"dimension": "structure_ordering", "rating": "adequate",
                 "findings": ["solid baseline"], "fixes": ["promote projects up"]},
            ],
            "overall_summary": "Solid baseline; minor reordering would help.",
        },
        alignment=None,
        reorganization={"section_order": ["summary", "experience", "skills"], "moves": []},
        rewrites=[],
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

    resume_reviewer = MagicMock(spec=ResumeReviewerAgent)
    resume_reviewer.run.side_effect = _make_resume_reviewer_side_effect

    resume_chat = MagicMock(spec=ResumeChatAgent)
    resume_chat.run.side_effect = _make_resume_chat_side_effect

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
        resume_reviewer=resume_reviewer,
        resume_chat=resume_chat,
        discovery_service=discovery_svc,
        resume_parser=resume_parser,
        report_generator=report_gen,
        job_repo=MagicMock(spec=JobRepository),
        score_repo=MagicMock(spec=ScoreRepository),
        advice_repo=MagicMock(spec=AdviceRepository),
        review_repo=MagicMock(spec=ReviewRepository),
        tailoring_repo=MagicMock(spec=TailoringRepository),
        resume_clinic_repo=MagicMock(spec=ResumeClinicRepository),
        workflow_repo=MagicMock(spec=WorkflowRepository),
        resume_repo=_mock_resume_repo(),
        observability=obs,
        checkpointer=checkpointer,
    )


# ── real deps (Phase 7 / ANTHROPIC_API_KEY set) ───────────────────────────────

def _build_real_deps(checkpointer) -> WorkflowDependencies:
    """Build WorkflowDependencies wired to real Claude agents and SQLite repos."""
    from pathlib import Path

    from app.providers.claude_provider import make_resume_enhance_fn
    from app.providers.model_registry import (
        ModelRegistry,
        catalog_from_config,
        defaults_from_config,
    )
    from app.providers.prompt_loader import PromptLoader

    # Anchor all paths to the project root (two levels up from app/api/dependencies.py)
    # so this works regardless of CWD (server, notebook, or test runner).
    _project_root = Path(__file__).resolve().parents[2]
    db_path = _project_root / "data" / "v2.db"

    # Ensure schema exists (CREATE TABLE IF NOT EXISTS — safe to call repeatedly)
    from app.repositories.database import init_db
    init_db(db_path)

    # ADR-062: wire the identity seam's existence validator to this DB so a
    # bogus user_id query param fails fast in production. Mock mode never reaches
    # here, so unit/mock tests keep accepting any id (validator stays unset).
    from app.api.identity import set_user_validator
    set_user_validator(UserRepository(db_path).exists)

    # Repositories — each manages its own connection via get_connection()
    job_repo = JobRepository(db_path)
    score_repo = ScoreRepository(db_path)
    advice_repo = AdviceRepository(db_path)
    review_repo = ReviewRepository(db_path)
    tailoring_repo = TailoringRepository(db_path)
    resume_clinic_repo = ResumeClinicRepository(db_path)
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
    # ADR-062: the startup registry uses the default profile's config. With
    # sequential use, per-active-user agent assignment is applied at run kickoff
    # (the per-workflow snapshot reads the acting user's config) and on profile
    # switch via reload; this is the sensible default base.
    _eff = config_svc_for_models.get_effective_config(DEFAULT_USER_ID)

    # Per-agent provider/model assignment (ADR-053 + ADR-058). Catalog, pricing,
    # and per-agent defaults all come from config (config.yaml `models:` and
    # `agents:` blocks). User overrides via the Settings UI are already merged
    # into _eff by ConfigService.
    catalog = catalog_from_config(_eff)
    agent_assignment = defaults_from_config(_eff)
    registry = ModelRegistry.build(loader, catalog, agent_assignment)
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
    resume_reviewer = ResumeReviewerAgent(registry.for_agent("resume_reviewer"), obs)
    resume_chat = ResumeChatAgent(registry.for_agent("resume_chat"), obs)

    # ResumeParser uses its own assigned provider too. Wire observability so the
    # LLM enhancement pass writes an llm_calls audit row when invoked from a
    # workflow run (the cost was previously dropped on the floor).
    enhance_fn = make_resume_enhance_fn(
        registry.for_agent("resume_parser"), observability=obs,
    )
    resume_parser = ResumeParser(resume_repo, enhance_fn=enhance_fn)

    config_dict = _eff

    # JobDiscoveryService with real scrapers (only include scrapers whose creds are present)
    scrapers = _build_scrapers(config_dict)
    discovery_svc = JobDiscoveryService(job_repo, config_dict, scrapers=scrapers)

    # ReportGenerator
    report_gen = ReportGenerator(score_repo, review_repo, advice_repo, tailoring_repo, report_repo, job_repo)

    # Custom URL scraper factory — built per workflow run with the user-supplied URLs.
    # observability + workflow_id are forwarded so the LLM-fallback extraction
    # writes to the run's llm_calls audit (otherwise its cost is invisible).
    from app.services.custom_url_scraper import CustomUrlScraper
    _custom_url_provider = registry.for_agent("custom_url_extractor")
    custom_url_factory = lambda urls, workflow_id: CustomUrlScraper(
        urls,
        llm_client=_custom_url_provider,
        observability=obs,
        workflow_id=workflow_id,
    )

    # ADR-064: per-run Adzuna scraper built from the run's search_criteria
    # (roles + locations), so a profile's own roles drive discovery instead of the
    # senior startup titles. Relevance is derived from the role tokens so non-senior
    # titles survive the gate. "Remote" in the locations list triggers a remote
    # search (no location filter) with the roles as keywords. Returns None when
    # creds are missing or no roles were given (caller falls back to the built-in).
    from app.services.concurrent_adzuna_scraper import (
        ConcurrentAdzunaScraper, relevance_tokens, SENIOR_TERMS,
        SENIOR_TERMS_API_EXCLUDE,
    )
    from models.config_schema import AdzunaConfig as _AdzunaConfig
    from models.filters import EXCLUDED_TITLE_KEYWORDS as _EXCLUDED
    _adzuna_raw = config_dict.get("scrapers", {}).get("adzuna", {})

    def _adzuna_factory(roles: list[str], locations: list[str],
                        exclude_senior: bool = False):
        if not roles:
            return None
        if not (os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY")):
            return None
        try:
            base = _AdzunaConfig(**_adzuna_raw)
            locs = locations or list(base.locations)
            physical = [l for l in locs if l.strip().lower() != "remote"]
            wants_remote = any(l.strip().lower() == "remote" for l in locs) or not physical
            cfg = base.model_copy(update={
                "locations": physical or list(base.locations),
                "remote_keywords": list(roles) if wants_remote else [],
            })
            # ADR-065: exclude senior roles at the source (what_exclude) and in the
            # per-run title gate (EXCLUDED + SENIOR_TERMS) when the profile opts in.
            # The two lists are deliberately different sizes (calibrated 2026-05-29):
            # - what_exclude is narrow (SENIOR_TERMS_API_EXCLUDE) because Adzuna
            #   matches it against title + description, so polysemic words like
            #   "manager"/"lead"/"staff" would drop entry-level postings.
            # - The local title gate uses the broader SENIOR_TERMS list because
            #   it's substring-matched against the TITLE only, where those same
            #   words are reliable seniority signals.
            what_exclude = SENIOR_TERMS_API_EXCLUDE if exclude_senior else None
            excluded_kw = (list(_EXCLUDED) + SENIOR_TERMS) if exclude_senior else None
            return ConcurrentAdzunaScraper.make(
                cfg, list(roles),
                relevant_keywords=relevance_tokens(roles),
                excluded_keywords=excluded_kw,
                what_exclude=what_exclude,
            )
        except Exception as exc:
            logger.warning("adzuna_scraper_factory failed: %s", exc)
            return None

    return WorkflowDependencies(
        research_agent=research,
        scoring_agent=scoring,
        resume_critic=critic,
        review_auditor=auditor,
        career_advisor=advisor,
        interview_coach=coach,
        tailoring_agent=tailoring,
        fidelity_reviewer=fidelity,
        resume_reviewer=resume_reviewer,
        resume_chat=resume_chat,
        discovery_service=discovery_svc,
        resume_parser=resume_parser,
        report_generator=report_gen,
        job_repo=job_repo,
        score_repo=score_repo,
        advice_repo=advice_repo,
        review_repo=review_repo,
        tailoring_repo=tailoring_repo,
        resume_clinic_repo=resume_clinic_repo,
        workflow_repo=workflow_repo,
        resume_repo=resume_repo,
        observability=obs,
        checkpointer=checkpointer,
        custom_url_scraper_factory=custom_url_factory,
        adzuna_scraper_factory=_adzuna_factory,
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
    global _graph, _deps, _cleanup_fn

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
    _deps = deps
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


def get_user_repo() -> UserRepository:
    """FastAPI dependency returning a UserRepository for the app's database.

    Anchored to the same project-root data/v2.db that _build_real_deps uses, so
    the /users endpoints read/write the same profiles the identity seam validates
    against. Tests override this dependency to point at a temp DB.
    """
    project_root = Path(__file__).resolve().parents[2]
    return UserRepository(project_root / "data" / "v2.db")


def get_deps() -> WorkflowDependencies:
    """FastAPI dependency that returns the cached WorkflowDependencies bundle.

    Used by routers that need direct access to a specific agent / repo (e.g. the
    on-demand tailoring router) without going through the LangGraph state machine.
    Tests can inject by overriding this dependency with a stub WorkflowDependencies.
    """
    if _deps is None:
        raise RuntimeError(
            "WorkflowDependencies not initialised. build_and_cache_graph() must be called at startup."
        )
    return _deps


def reload_deps_and_graph() -> dict:
    """Atomically rebuild WorkflowDependencies and the graph from current user_config.

    Used by POST /config/reload (ADR-053 addendum) so a UI Settings save can take
    effect without a process restart. Steps:

      1. Snapshot the old cleanup fn so the old SqliteSaver gets released after
         the new one is wired up.
      2. Run the same build path startup uses (`build_and_cache_graph`) — this
         reads user_config fresh and produces a new ModelRegistry, agents, and
         compiled graph.
      3. Release the old SqliteSaver / connection holder.

    In-flight workflows: their LangGraph runner already holds a reference to the
    OLD graph + agents; it continues using them until the run completes. Only
    workflows started AFTER this call use the new assignment.

    Returns: { "agent_assignment": {agent: {provider, model}, ...} } so the
    caller can show the new effective config without a follow-up GET.

    Restart is still required for: prompt file changes (PromptLoader caches
    files at first read), code changes, and provider client initialisation
    bugs that need a fresh interpreter.
    """
    global _cleanup_fn
    old_cleanup = _cleanup_fn
    _cleanup_fn = None  # build_and_cache_graph will install the new one

    build_and_cache_graph()

    # Release the prior SqliteSaver only after the new graph is fully wired.
    # Order matters: any new request landing during the rebuild will block on
    # the FastAPI dependency injection until _graph / _deps are reassigned, so
    # there is never a window where get_graph() returns None.
    if old_cleanup is not None:
        try:
            old_cleanup()
        except Exception:
            logger.exception("reload_deps_and_graph: old cleanup_fn raised — continuing")

    # Read the live assignment off each agent's provider so the response
    # mirrors what the just-built graph will actually use. Tolerates mocked
    # providers (test doubles) by skipping any agent whose provider doesn't
    # expose provider_name / model_name.
    assignment: dict[str, dict[str, str]] = {}
    if _deps is not None:
        for agent_name, attr in (
            ("research_agent",   "research_agent"),
            ("scoring_agent",    "scoring_agent"),
            ("resume_critic",    "resume_critic"),
            ("review_auditor",   "review_auditor"),
            ("career_advisor",   "career_advisor"),
            ("interview_coach",  "interview_coach"),
            ("tailoring_agent",  "tailoring_agent"),
            ("fidelity_reviewer","fidelity_reviewer"),
        ):
            try:
                agent = getattr(_deps, attr, None)
                if agent is None:
                    continue
                provider = getattr(agent, "_provider", None)
                if provider is None:
                    continue
                pname = getattr(provider, "provider_name", None)
                mname = getattr(provider, "model_name", None)
                if pname and mname:
                    assignment[agent_name] = {"provider": pname, "model": mname}
            except Exception:
                logger.exception("reload_deps_and_graph: could not read assignment for %s", agent_name)

    return {"agent_assignment": assignment}
