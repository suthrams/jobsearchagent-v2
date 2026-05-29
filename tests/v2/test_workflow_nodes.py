"""Tests for individual workflow nodes.

All tests use mocked agents and services — no real LLM calls, no real DB.
Each node factory is called with mocks and the returned node function is
invoked with a minimal state dict. Assertions are on the returned partial dict.
"""
import pytest
from unittest.mock import MagicMock, patch

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
from app.workflows.nodes.career_advice import make_career_advice_node
from app.workflows.nodes.deep_review import make_deep_review_node
from app.workflows.nodes.discover_jobs import make_discover_jobs_node
from app.workflows.nodes.generate_report import make_generate_report_node
from app.workflows.nodes.interview_prep import make_interview_prep_node
from app.workflows.nodes.score_jobs import make_score_jobs_node
from app.workflows.routers import interview_router


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _obs() -> MagicMock:
    return MagicMock(spec=ObservabilityService)

def _job_repo() -> MagicMock:
    return MagicMock(spec=JobRepository)

def _score_repo() -> MagicMock:
    return MagicMock(spec=ScoreRepository)

def _advice_repo() -> MagicMock:
    return MagicMock(spec=AdviceRepository)

def _review_repo() -> MagicMock:
    return MagicMock(spec=ReviewRepository)

def _tailoring_repo() -> MagicMock:
    return MagicMock(spec=TailoringRepository)

def _base_state(**overrides) -> dict:
    state = {
        "workflow_id": "wf-test-001",
        "resume_id": "res-001",
        "resume_profile": {"name": "Test User", "skills": ["Python"]},
        "search_criteria": {"roles": ["Staff Engineer"]},
        "normalized_jobs": [],
        "scored_jobs": [],
        "selected_jobs": [],
        "run_metrics": {"llm_calls": 0, "tokens_input": 0, "tokens_output": 0, "estimated_cost_usd": 0.0},
        "errors": [],
        "effective_config": {"scoring": {"career_track": "all"}},
    }
    state.update(overrides)
    return state

def _make_job(job_id: str = "job-001", score: int = 80) -> dict:
    return {
        "id": job_id,
        "job_id": job_id,
        "title": "Staff Engineer",
        "company": "Acme",
        "location": "Remote",
        "job_description": "Python, distributed systems.",
        "url": "https://example.com/job",
        "status": "scored",
        "overall_score": score,
    }

def _score_result(job_id: str = "job-001") -> dict:
    return {
        "job_id": job_id, "resume_id": "res-001",
        "overall_score": 80, "technical_score": 85,
        "architecture_score": 75, "leadership_score": 60, "domain_score": 70,
        "match_summary": "Good fit.", "strengths": ["Python"], "gaps": [],
        "recommended_next_action": "Apply.", "confidence": 82,
    }

def _research_result(job_id: str = "job-001") -> dict:
    return {
        "job_id": job_id,
        "company_summary": "Tech co.", "role_context": "Platform team.",
        "technology_signals": ["Python"], "leadership_signals": [],
        "domain_signals": [], "risk_flags": [],
        "research_steps": [], "confidence": 75,
    }

def _review_result(job_id: str = "job-001") -> dict:
    return {
        "job_id": job_id, "resume_id": "res-001",
        "overall_fit_summary": "Good.",
        "section_reviews": [],
        "critical_gaps": [], "resume_only_gaps": [], "career_gaps_observed": [],
        "suggested_improvements": [], "questions_for_user": [], "confidence": 80,
    }

def _audit_result(score: int = 80, stop: bool = True) -> dict:
    return {
        "job_id": "job-001", "round_number": 1,
        "audit_score": score, "auditor_confidence": 80,
        "quality_summary": "OK.", "missing_analysis_points": [],
        "generic_or_weak_feedback": [], "unsupported_claims": [],
        "fidelity_concerns": [], "recommended_revision_instructions": [] if stop else ["Add quantified impact."],
        "stop_recommendation": stop, "stop_reason": "Threshold reached.",
    }

def _advice_result() -> dict:
    return {
        "job_id": "job-001",
        "positioning_summary": "Good.", "resume_gaps": [], "career_gaps": [],
        "role_fit_assessment": "High.", "recommended_positioning": "Lead with Python.",
        "skills_to_strengthen": [], "experience_to_collect": [],
        "thirty_sixty_ninety_day_plan": [], "recommended_next_action": "Apply.",
        "confidence": 82,
    }

def _prep_result() -> dict:
    return {
        "job_id": "job-001",
        "likely_interview_topics": ["System design"],
        "technical_topics_to_review": ["Distributed systems"],
        "leadership_stories_to_prepare": [], "weak_areas_to_defend": [],
        "questions_to_ask_interviewer": [], "seven_day_prep_plan": [], "confidence": 80,
    }

def _draft_result() -> dict:
    return {
        "job_id": "job-001", "resume_id": "res-001",
        "summary_suggestions": [], "experience_bullet_suggestions": [],
        "skills_section_suggestions": [],
        "overall_tailoring_notes": "Good.", "fidelity_risk_summary": "Low.",
    }

def _fidelity_result() -> dict:
    return {
        "job_id": "job-001", "resume_id": "res-001",
        "overall_fidelity_status": "pass",
        "unsupported_claims": [], "fabricated_metrics": [],
        "inflated_scope_flags": [], "unsupported_technology_flags": [],
        "unsupported_certification_flags": [], "required_removals": [],
        "required_revisions": [], "approval_recommendation": "approve", "confidence": 95,
    }


# ── discover_jobs ──────────────────────────────────────────────────────────────

def test_discover_jobs_happy_path_returns_normalized_jobs():
    from app.schemas.job_posting import JobPosting, JobSource, WorkMode
    from app.repositories.database import utcnow_iso

    posting = JobPosting(
        job_id="job-001", workflow_id="wf-test-001",
        url="https://example.com", source=JobSource.MANUAL,
        title="Staff Engineer", company="Acme",
        work_mode=WorkMode.REMOTE, description="Python role.",
        found_at=utcnow_iso(),
    )
    svc = MagicMock(spec=JobDiscoveryService)
    svc.discover.return_value = [posting]
    repo = _job_repo()

    node = make_discover_jobs_node(svc, repo, _obs())
    result = node(_base_state())

    assert len(result["normalized_jobs"]) == 1
    assert result["normalized_jobs"][0]["id"] == "job-001"
    assert result["normalized_jobs"][0]["status"] == "discovered"
    assert result["current_step"] == "job_discovery"


def test_discover_jobs_service_error_returns_empty_with_error_appended():
    svc = MagicMock(spec=JobDiscoveryService)
    svc.discover.side_effect = RuntimeError("scraper blocked")

    node = make_discover_jobs_node(svc, _job_repo(), _obs())
    result = node(_base_state())

    assert result["normalized_jobs"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["error_type"] == "discovery_error"


def test_discover_jobs_caps_at_max_jobs():
    from app.schemas.job_posting import JobPosting, JobSource, WorkMode
    from app.repositories.database import utcnow_iso
    from app.workflows.limits import MAX_JOBS_PER_RUN

    postings = [
        JobPosting(
            job_id=f"job-{i:03d}", workflow_id="wf-test-001",
            url=f"https://example.com/{i}", source=JobSource.MANUAL,
            title="Engineer", company="Acme",
            work_mode=WorkMode.REMOTE, description="Role.",
            found_at=utcnow_iso(),
        )
        for i in range(MAX_JOBS_PER_RUN + 5)
    ]
    svc = MagicMock(spec=JobDiscoveryService)
    svc.discover.return_value = postings

    node = make_discover_jobs_node(svc, _job_repo(), _obs())
    result = node(_base_state())

    assert len(result["normalized_jobs"]) == MAX_JOBS_PER_RUN


# ── score_jobs ─────────────────────────────────────────────────────────────────

def _make_research_agent(result: dict | None = None) -> MagicMock:
    mock = MagicMock(spec=ResearchAgent)
    mock.run.return_value = ResearchContext(**(result or _research_result()))
    return mock

def _make_scoring_agent(result: dict | None = None, job_id: str = "job-001") -> MagicMock:
    mock = MagicMock(spec=ScoringAgent)
    mock.run.return_value = JobScore(**(result or _score_result(job_id)))
    return mock


def test_score_jobs_happy_path_sets_status_scored():
    state = _base_state(normalized_jobs=[_make_job("job-001", 0)])
    node = make_score_jobs_node(_make_research_agent(), _make_scoring_agent(), _score_repo(), _obs())
    result = node(state)

    assert len(result["scored_jobs"]) == 1
    assert result["scored_jobs"][0]["status"] == "scored"
    assert result["scored_jobs"][0]["overall_score"] == 80


def test_score_jobs_scoring_error_marks_job_failed_and_continues():
    scoring = MagicMock(spec=ScoringAgent)
    scoring.run.side_effect = LLMProviderError("timeout")

    state = _base_state(normalized_jobs=[_make_job("job-001"), _make_job("job-002")])
    node = make_score_jobs_node(_make_research_agent(), scoring, _score_repo(), _obs())
    result = node(state)

    assert len(result["scored_jobs"]) == 2
    assert result["scored_jobs"][0]["status"] == "scoring_failed"
    assert result["scored_jobs"][1]["status"] == "scoring_failed"


def test_score_jobs_research_error_marks_job_research_failed():
    research = MagicMock(spec=ResearchAgent)
    research.run.side_effect = LLMProviderError("research fail")

    state = _base_state(normalized_jobs=[_make_job("job-001")])
    node = make_score_jobs_node(research, _make_scoring_agent(), _score_repo(), _obs())
    result = node(state)

    assert result["scored_jobs"][0]["status"] == "research_failed"
    assert len(result["errors"]) == 1


def test_score_jobs_budget_exhausted_marks_remaining_skipped():
    from app.workflows.limits import MAX_LLM_CALLS_PER_RUN
    state = _base_state(
        normalized_jobs=[_make_job("job-001"), _make_job("job-002")],
        run_metrics={"llm_calls": MAX_LLM_CALLS_PER_RUN, "tokens_input": 0,
                     "tokens_output": 0, "estimated_cost_usd": 0.0},
    )
    node = make_score_jobs_node(_make_research_agent(), _make_scoring_agent(), _score_repo(), _obs())
    result = node(state)

    statuses = [j["status"] for j in result["scored_jobs"]]
    assert all(s == "budget_skipped" for s in statuses)


def test_score_jobs_passes_resume_profile_via_cached_block():
    """Invariant: the scoring_agent's context must carry resume_profile under
    `_cached`, not as a top-level per-call field. PromptLoader routes
    `_cached` into the cache_control system block; routing it elsewhere
    misses Anthropic's 5-minute prompt cache and is the cost regression
    diagnosed on 2026-05-29 (scoring at 0% cache hit ratio).

    This test catches the regression mechanically: if a future refactor
    moves resume_profile back to the per-call dict, this test fails."""
    state = _base_state(normalized_jobs=[_make_job("job-001")])
    scoring = _make_scoring_agent()
    node = make_score_jobs_node(_make_research_agent(), scoring, _score_repo(), _obs())
    node(state)
    assert scoring.run.called
    _, ctx = scoring.run.call_args[0]
    assert "_cached" in ctx, "scoring_agent must receive resume_profile via _cached"
    assert "resume_profile" in ctx["_cached"]
    assert "resume_profile" not in ctx, \
        "resume_profile must NOT also appear at top level (would duplicate tokens)"


def test_score_jobs_multiple_jobs_all_scored():
    jobs = [_make_job(f"job-{i:03d}") for i in range(3)]
    state = _base_state(normalized_jobs=jobs)

    call_count = [0]
    def make_score(job_id):
        call_count[0] += 1
        return JobScore(**_score_result(job_id))

    scoring = MagicMock(spec=ScoringAgent)
    scoring.run.side_effect = lambda wf_id, ctx: make_score(ctx["job_id"])

    node = make_score_jobs_node(_make_research_agent(), scoring, _score_repo(), _obs())
    result = node(state)

    assert len(result["scored_jobs"]) == 3
    assert all(j["status"] == "scored" for j in result["scored_jobs"])


# ── deep_review ────────────────────────────────────────────────────────────────

def _make_critic(result: dict | None = None) -> MagicMock:
    mock = MagicMock(spec=ResumeCritic)
    mock.run.return_value = ResumeReview(**(result or _review_result()))
    return mock

def _make_auditor(score: int = 82, stop: bool = True) -> MagicMock:
    mock = MagicMock(spec=ReviewAuditor)
    mock.run.return_value = ReviewAudit(**_audit_result(score=score, stop=stop))
    return mock


def test_deep_review_happy_path_returns_final_review():
    state = _base_state(
        selected_jobs=[_make_job("job-001")],
        scored_jobs=[_make_job("job-001")],
    )
    node = make_deep_review_node(_make_critic(), _make_auditor(stop=True), _review_repo(), _obs())
    result = node(state)

    assert result["final_resume_review"] is not None
    assert len(result["review_rounds"]) == 1
    assert result["current_step"] == "review_completed"


def test_deep_review_stagnation_exits_loop():
    from app.workflows.limits import MAX_REVIEW_ROUNDS

    audit_results = [
        ReviewAudit(**_audit_result(score=60, stop=False)),
        ReviewAudit(**_audit_result(score=62, stop=False)),  # improvement = 2 < 5 → stagnation
    ]
    auditor = MagicMock(spec=ReviewAuditor)
    auditor.run.side_effect = audit_results

    state = _base_state(
        selected_jobs=[_make_job("job-001")],
        scored_jobs=[_make_job("job-001")],
    )
    node = make_deep_review_node(_make_critic(), auditor, _review_repo(), _obs())
    result = node(state)

    # Stagnation detected after round 2 — loop stopped
    assert len(result["review_rounds"]) == 2
    assert result["final_resume_review"] is not None


def test_deep_review_critic_error_marks_job_failed():
    critic = MagicMock(spec=ResumeCritic)
    critic.run.side_effect = LLMProviderError("critic fail")

    state = _base_state(
        selected_jobs=[_make_job("job-001")],
        scored_jobs=[_make_job("job-001")],
    )
    node = make_deep_review_node(critic, _make_auditor(), _review_repo(), _obs())
    result = node(state)

    assert len(result["errors"]) == 1
    assert result["errors"][0]["error_type"] == "critic_failed"


def test_deep_review_stops_at_max_rounds():
    from app.workflows.limits import MAX_REVIEW_ROUNDS

    # Auditor never recommends stop, score always improves enough to avoid stagnation
    auditor = MagicMock(spec=ReviewAuditor)
    auditor.run.side_effect = [
        ReviewAudit(**_audit_result(score=50 + i * 10, stop=False))
        for i in range(MAX_REVIEW_ROUNDS + 2)
    ]
    state = _base_state(
        selected_jobs=[_make_job("job-001")],
        scored_jobs=[_make_job("job-001")],
    )
    node = make_deep_review_node(_make_critic(), auditor, _review_repo(), _obs())
    result = node(state)

    assert len(result["review_rounds"]) <= MAX_REVIEW_ROUNDS


def test_deep_review_runs_jobs_concurrently():
    """Regression for the ThreadPoolExecutor refactor.

    With 5 selected jobs and a 50 ms sleep in each agent call, sequential would
    take >= 500 ms (5 jobs * (50 ms critic + 50 ms auditor)). Parallel with 5
    workers should complete in roughly one job's worth of time. Asserting under
    300 ms gives a generous margin for CI variance while still failing if the
    node ever regresses to sequential.
    """
    import time

    def _slow_critic_run(workflow_id, context):
        time.sleep(0.05)
        return ResumeReview(**_review_result(job_id=context.get("job_id", "job-001")))

    def _slow_auditor_run(workflow_id, context):
        time.sleep(0.05)
        return ReviewAudit(**{**_audit_result(score=85, stop=True),
                              "job_id": context.get("job_id", "job-001")})

    critic = MagicMock(spec=ResumeCritic)
    critic.run.side_effect = _slow_critic_run

    auditor = MagicMock(spec=ReviewAuditor)
    auditor.run.side_effect = _slow_auditor_run

    jobs = [_make_job(f"job-{i:03d}") for i in range(1, 6)]
    state = _base_state(selected_jobs=jobs, scored_jobs=jobs)
    node = make_deep_review_node(critic, auditor, _review_repo(), _obs())

    start = time.monotonic()
    result = node(state)
    elapsed = time.monotonic() - start

    assert len(result["review_rounds"]) == 5, "expected one round per job"
    assert elapsed < 0.3, (
        f"deep_review took {elapsed:.3f}s with 5 jobs * 100ms — looks sequential "
        f"(should be ~0.1s with 5 parallel workers)"
    )


# ── career_advice ──────────────────────────────────────────────────────────────

def _make_advisor(result: dict | None = None) -> MagicMock:
    mock = MagicMock(spec=CareerAdvisor)
    mock.run.return_value = CareerAdvice(**(result or _advice_result()))
    return mock


def test_career_advice_happy_path():
    state = _base_state(
        selected_jobs=[_make_job("job-001")],
        scored_jobs=[_make_job("job-001")],
        final_resume_review=_review_result(),
    )
    node = make_career_advice_node(_make_advisor(), _advice_repo(), _obs())
    result = node(state)

    assert result["career_advice"] is not None
    assert result["career_advice"]["positioning_summary"] == "Good."


def test_career_advice_provider_error_returns_none():
    advisor = MagicMock(spec=CareerAdvisor)
    advisor.run.side_effect = LLMProviderError("fail")

    state = _base_state(
        selected_jobs=[_make_job("job-001")],
        scored_jobs=[_make_job("job-001")],
    )
    node = make_career_advice_node(advisor, _advice_repo(), _obs())
    result = node(state)

    assert result["career_advice"] is None
    assert len(result["errors"]) == 1


def test_career_advice_no_selected_jobs_returns_none():
    state = _base_state(selected_jobs=[])
    node = make_career_advice_node(_make_advisor(), _advice_repo(), _obs())
    result = node(state)
    assert result["career_advice"] is None


# ── interview_prep ─────────────────────────────────────────────────────────────

def _make_coach(result: dict | None = None) -> MagicMock:
    mock = MagicMock(spec=InterviewCoach)
    mock.run.return_value = InterviewPrep(**(result or _prep_result()))
    return mock


def _make_scored_job(job_id: str = "job-001", *,
                     technical: int = 0, architecture: int = 0,
                     leadership: int = 0, overall: int = 0) -> dict:
    """Builds a scored job dict with explicit per-track scores for threshold tests."""
    return {
        "id": job_id, "job_id": job_id,
        "title": "Staff Engineer", "company": "Acme",
        "location": "Remote", "job_description": "Python.",
        "url": "https://example.com/job", "status": "scored",
        "overall_score": overall,
        "technical_score": technical,
        "architecture_score": architecture,
        "leadership_score": leadership,
        "domain_score": 0,
    }


def test_interview_prep_high_track_score_triggers_coach():
    """Any track score >= threshold triggers the coach."""
    job = _make_scored_job("job-001", architecture=80)  # only architecture qualifies
    state = _base_state(selected_jobs=[job], scored_jobs=[job])
    node = make_interview_prep_node(_make_coach(), _advice_repo(), _obs())
    result = node(state)
    assert result["interview_prep"] is not None


def test_interview_prep_low_score_no_request_is_noop():
    job = _make_scored_job("job-001", technical=50, architecture=50, leadership=50)
    state = _base_state(
        selected_jobs=[job], scored_jobs=[job],
        user_requested_interview_prep=False,
    )
    coach = MagicMock(spec=InterviewCoach)
    node = make_interview_prep_node(coach, _advice_repo(), _obs())
    node(state)
    coach.run.assert_not_called()


def test_interview_prep_user_request_overrides_threshold():
    job = _make_scored_job("job-001", technical=30, architecture=30, leadership=30)
    state = _base_state(
        selected_jobs=[job], scored_jobs=[job],
        user_requested_interview_prep=True,
    )
    node = make_interview_prep_node(_make_coach(), _advice_repo(), _obs())
    result = node(state)
    assert result["interview_prep"] is not None


def test_interview_prep_custom_threshold_from_config():
    """Per-run min_match_score in effective_config overrides the default."""
    job = _make_scored_job("job-001", technical=70)  # above 65, below default 75
    state = _base_state(
        selected_jobs=[job], scored_jobs=[job],
        effective_config={"scoring": {"min_match_score": 65}},
    )
    node = make_interview_prep_node(_make_coach(), _advice_repo(), _obs())
    result = node(state)
    assert result["interview_prep"] is not None


# ── tailoring ──────────────────────────────────────────────────────────────────

def _make_tailoring_agent(result: dict | None = None) -> MagicMock:
    mock = MagicMock(spec=TailoringAgent)
    mock.run.return_value = TailoredResumeDraft(**(result or _draft_result()))
    return mock

def _make_fidelity_reviewer(result: dict | None = None) -> MagicMock:
    mock = MagicMock(spec=FidelityReviewer)
    mock.run.return_value = FidelityReview(**(result or _fidelity_result()))
    return mock


# Tailoring is an out-of-graph operation (ADR-055); the in-graph tailoring node
# was retired in ADR-059. Its behavior is covered by tests/v2/test_tailoring_router.py.


# ── generate_report ────────────────────────────────────────────────────────────

def test_generate_report_happy_path():
    rg = MagicMock(spec=ReportGenerator)
    rg.generate_run_summary.return_value = "# Report\nContent."
    node = make_generate_report_node(rg, _obs())
    result = node(_base_state())

    assert result["report"]["markdown"] == "# Report\nContent."
    assert result["status"] == "completed"
    assert result["current_step"] == "completed"


def test_generate_report_error_sets_completed_with_errors():
    rg = MagicMock(spec=ReportGenerator)
    rg.generate_run_summary.side_effect = RuntimeError("DB error")
    node = make_generate_report_node(rg, _obs())
    result = node(_base_state())

    assert result["status"] == "completed_with_errors"
    assert len(result["errors"]) == 1


# ── routers ────────────────────────────────────────────────────────────────────

def test_interview_router_high_track_score_returns_interview_prep():
    """Any single track score >= threshold qualifies."""
    state = {"selected_jobs": [{"architecture_score": 90, "technical_score": 30, "leadership_score": 20}]}
    assert interview_router(state) == "interview_prep"


def test_interview_router_low_score_returns_generate_report():
    state = {
        "selected_jobs": [{"technical_score": 40, "architecture_score": 40, "leadership_score": 40}],
        "user_requested_interview_prep": False,
    }
    assert interview_router(state) == "generate_report"


def test_interview_router_user_request_overrides_score():
    state = {
        "selected_jobs": [{"technical_score": 10}],
        "user_requested_interview_prep": True,
    }
    assert interview_router(state) == "interview_prep"


def test_interview_router_respects_custom_threshold():
    state = {
        "selected_jobs": [{"technical_score": 60}],
        "effective_config": {"scoring": {"min_match_score": 50}},
    }
    assert interview_router(state) == "interview_prep"


