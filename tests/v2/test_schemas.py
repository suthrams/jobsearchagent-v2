import pytest
from pydantic import ValidationError

from app.schemas.career_advice import CareerAdvice
from app.schemas.fidelity_review import FidelityReview
from app.schemas.interview_prep import InterviewPrep
from app.schemas.job_score import JobScore
from app.schemas.research_context import ResearchContext, ResearchStep
from app.schemas.resume_review import ResumeReview, SectionReview
from app.schemas.review_audit import ReviewAudit
from app.schemas.tailored_resume_draft import TailoredBullet, TailoredResumeDraft
from app.state.workflow_state import (
    HumanDecision,
    RunMetrics,
    StepExecution,
    WorkflowError,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)


# ─── WorkflowStatus ──────────────────────────────────────────────────────────

def test_workflow_status_valid_values():
    assert WorkflowStatus.INITIALIZED == "initialized"
    assert WorkflowStatus.RUNNING == "running"
    assert WorkflowStatus.WAITING_FOR_USER == "waiting_for_user"
    assert WorkflowStatus.COMPLETED == "completed"
    assert WorkflowStatus.FAILED == "failed"
    assert WorkflowStatus.CANCELLED == "cancelled"


def test_workflow_step_valid_values():
    assert WorkflowStep.JOB_DISCOVERY == "job_discovery"
    assert WorkflowStep.SCORING == "scoring"
    assert WorkflowStep.TAILORING == "tailoring"


# ─── StepExecution ───────────────────────────────────────────────────────────

def test_step_execution_valid():
    s = StepExecution(
        step=WorkflowStep.SCORING,
        status="started",
        started_at="2026-04-28T10:00:00.000Z",
    )
    assert s.completed_at is None
    assert s.duration_ms is None


def test_step_execution_completed():
    s = StepExecution(
        step=WorkflowStep.SCORING,
        status="completed",
        started_at="2026-04-28T10:00:00.000Z",
        completed_at="2026-04-28T10:00:02.500Z",
        duration_ms=2500,
    )
    assert s.duration_ms == 2500


def test_step_execution_requires_step_and_started_at():
    with pytest.raises(ValidationError):
        StepExecution(status="started", started_at="2026-04-28T10:00:00.000Z")
    with pytest.raises(ValidationError):
        StepExecution(step=WorkflowStep.SCORING, status="started")


# ─── RunMetrics ──────────────────────────────────────────────────────────────

def test_run_metrics_defaults():
    m = RunMetrics()
    assert m.llm_calls == 0
    assert m.estimated_cost_usd == 0.0
    assert m.started_at is None
    assert m.completed_at is None


def test_run_metrics_with_timestamps():
    m = RunMetrics(
        started_at="2026-04-28T10:00:00.000Z",
        completed_at="2026-04-28T10:05:00.000Z",
        total_duration_ms=300000,
    )
    assert m.started_at == "2026-04-28T10:00:00.000Z"
    assert m.total_duration_ms == 300000


# ─── WorkflowError ───────────────────────────────────────────────────────────

def test_workflow_error_requires_occurred_at():
    with pytest.raises(ValidationError):
        WorkflowError(step="scoring", error_type="llm_failure",
                      message="timeout", recoverable=True)


def test_workflow_error_valid():
    e = WorkflowError(
        step="scoring",
        error_type="llm_failure",
        message="timeout",
        recoverable=True,
        occurred_at="2026-04-28T10:00:00.000Z",
    )
    assert e.recoverable is True
    assert e.suggested_action is None


# ─── HumanDecision ───────────────────────────────────────────────────────────

def test_human_decision_requires_both_timestamps():
    with pytest.raises(ValidationError):
        HumanDecision(decision_type="select_jobs", decision_value="approve",
                      presented_at="2026-04-28T10:00:00.000Z")
    with pytest.raises(ValidationError):
        HumanDecision(decision_type="select_jobs", decision_value="approve",
                      decided_at="2026-04-28T10:01:00.000Z")


def test_human_decision_valid():
    d = HumanDecision(
        decision_type="select_jobs_for_deep_review",
        decision_value="approve",
        presented_at="2026-04-28T10:00:00.000Z",
        decided_at="2026-04-28T10:01:30.000Z",
    )
    assert d.payload == {}


# ─── WorkflowState ───────────────────────────────────────────────────────────

def _make_state(**kwargs) -> dict:
    base = dict(
        workflow_id="wf_001",
        workflow_type="full_career_review",
        status=WorkflowStatus.INITIALIZED,
        current_step=WorkflowStep.INITIALIZED,
        created_at="2026-04-28T10:00:00.000Z",
        updated_at="2026-04-28T10:00:00.000Z",
    )
    base.update(kwargs)
    return base


def test_workflow_state_valid():
    state = WorkflowState(**_make_state())
    assert state.workflow_id == "wf_001"
    assert state.step_history == []
    assert state.errors == []
    assert state.run_metrics.llm_calls == 0


def test_workflow_state_requires_workflow_id():
    data = _make_state()
    del data["workflow_id"]
    with pytest.raises(ValidationError):
        WorkflowState(**data)


def test_workflow_state_invalid_status():
    with pytest.raises(ValidationError):
        WorkflowState(**_make_state(status="not_a_status"))


def test_workflow_state_step_history_append():
    state = WorkflowState(**_make_state())
    step = StepExecution(
        step=WorkflowStep.SCORING,
        status="started",
        started_at="2026-04-28T10:00:00.000Z",
    )
    state.step_history.append(step)
    assert len(state.step_history) == 1
    assert state.step_history[0].step == WorkflowStep.SCORING


# ─── JobScore ────────────────────────────────────────────────────────────────

def test_job_score_valid():
    s = JobScore(
        job_id="job_001", resume_id="res_001",
        overall_score=85, technical_score=90, architecture_score=80,
        leadership_score=75, domain_score=88,
        match_summary="Strong match", strengths=["cloud"], gaps=["IoT"],
        recommended_next_action="Apply", confidence=90,
    )
    assert s.overall_score == 85


def test_job_score_rejects_out_of_range():
    with pytest.raises(ValidationError):
        JobScore(
            job_id="j", resume_id="r",
            overall_score=101, technical_score=80, architecture_score=80,
            leadership_score=80, domain_score=80,
            match_summary="x", strengths=[], gaps=[],
            recommended_next_action="x", confidence=80,
        )
    with pytest.raises(ValidationError):
        JobScore(
            job_id="j", resume_id="r",
            overall_score=80, technical_score=-1, architecture_score=80,
            leadership_score=80, domain_score=80,
            match_summary="x", strengths=[], gaps=[],
            recommended_next_action="x", confidence=80,
        )


# ─── ResearchContext ─────────────────────────────────────────────────────────

def test_research_context_valid():
    ctx = ResearchContext(
        job_id="job_001",
        company_summary="Energy company",
        role_context="Principal Architect",
        technology_signals=["AWS", "Kubernetes"],
        leadership_signals=["cross-functional"],
        domain_signals=["utilities"],
        risk_flags=[],
        research_steps=[
            ResearchStep(step_number=1, tool_used="job_fetcher",
                         observation_summary="Fetched JD")
        ],
        confidence=75,
    )
    assert len(ctx.research_steps) == 1


# ─── ResearchContext Haiku-emission-quirk regression tests ───────────────────
# A previous run failed because Haiku emitted leadership_signals as a
# JSON-encoded STRING ('["Head of...", "CTO..."]') instead of a real list.
# These tests pin the coercion behavior.

def test_research_context_coerces_jsonish_string_to_list():
    """The exact failing shape from production: a list[str] field arrives as
    a JSON-encoded string. The validator must parse it back to a real list."""
    ctx = ResearchContext.model_validate({
        "job_id": "j1",
        "company_summary": "A SaaS company",
        "role_context": "Director of Engineering",
        # Haiku quirk: stringified array
        "leadership_signals": '["Head of title indicates leadership", "CTO or VP Engineering"]',
        "technology_signals": ["AWS"],   # normal list works alongside
    })
    assert ctx.leadership_signals == [
        "Head of title indicates leadership",
        "CTO or VP Engineering",
    ]
    assert ctx.technology_signals == ["AWS"]


def test_research_context_coerces_all_four_signal_lists():
    """Apply coercion to every list[str] field, not just leadership_signals."""
    ctx = ResearchContext.model_validate({
        "job_id": "j2",
        "company_summary": "Test",
        "role_context": "Test",
        "technology_signals": '["Python", "Go"]',
        "leadership_signals": '["IC track"]',
        "domain_signals": '["Fintech"]',
        "risk_flags": '["Recent layoffs"]',
    })
    assert ctx.technology_signals == ["Python", "Go"]
    assert ctx.leadership_signals == ["IC track"]
    assert ctx.domain_signals == ["Fintech"]
    assert ctx.risk_flags == ["Recent layoffs"]


def test_research_context_wraps_non_json_string_in_one_item_list():
    """Worst-case fallback: if the value is a string but not JSON-list-shaped,
    wrap it in a one-item list rather than rejecting."""
    ctx = ResearchContext.model_validate({
        "job_id": "j3",
        "company_summary": "Test",
        "role_context": "Test",
        "leadership_signals": "Single freeform observation",
    })
    assert ctx.leadership_signals == ["Single freeform observation"]


def test_research_context_empty_string_becomes_empty_list():
    """Empty string -> empty list, not [''] or rejection."""
    ctx = ResearchContext.model_validate({
        "job_id": "j4",
        "company_summary": "Test",
        "role_context": "Test",
        "leadership_signals": "",
    })
    assert ctx.leadership_signals == []


def test_research_context_accepts_minimal_partial_response():
    """Tolerance: optional fields default if missing, just like ResumeReview."""
    ctx = ResearchContext.model_validate({
        "job_id": "j5",
        "company_summary": "Stealth-mode startup; little public info.",
        "role_context": "Senior backend role.",
        # All signal lists, research_steps, confidence omitted
    })
    assert ctx.technology_signals == []
    assert ctx.leadership_signals == []
    assert ctx.domain_signals == []
    assert ctx.risk_flags == []
    assert ctx.research_steps == []
    assert ctx.confidence == 0


def test_research_context_still_rejects_load_bearing_omissions():
    """Tolerance is strictly limited; required fields are still required."""
    import pytest as _pytest
    with _pytest.raises(Exception):
        ResearchContext.model_validate({
            "job_id": "j6",
            # company_summary missing
            "role_context": "x",
        })


# ─── ResumeReview ────────────────────────────────────────────────────────────

def test_resume_review_gap_separation():
    review = ResumeReview(
        job_id="job_001", resume_id="res_001",
        overall_fit_summary="Good match",
        section_reviews=[
            SectionReview(
                section_name="Experience",
                current_issue="Weak cloud bullets",
                why_it_matters="Job requires cloud",
                improvement_opportunity="Reframe existing cloud work",
                suggested_direction="Use cloud terminology",
                evidence="Has AWS experience in role 2",
                risk_level="medium",
            )
        ],
        critical_gaps=["No Kubernetes mention"],
        resume_only_gaps=["Cloud experience not expressed"],
        career_gaps_observed=["No formal architecture certification"],
        suggested_improvements=["Reframe AWS bullets"],
        questions_for_user=["Which cloud projects were most impactful?"],
        confidence=80,
    )
    assert "Cloud experience not expressed" in review.resume_only_gaps
    assert "No formal architecture certification" in review.career_gaps_observed


# ─── ResumeReview tolerance (regression for Haiku-omits-fields error) ────────
# A previous run failed with "career_gaps_observed Field required" when Haiku
# emitted a partial response. The schema now defaults the tolerant fields to
# empty list / 0 so the load-bearing analysis isn't thrown away.

def test_resume_review_accepts_minimal_partial_response():
    """Only the load-bearing fields are required. Tolerant fields default."""
    review = ResumeReview(
        job_id="job_002", resume_id="res_002",
        overall_fit_summary="Strong fit on core; some gaps.",
        section_reviews=[],
        critical_gaps=["No SOC 2 experience"],
        # career_gaps_observed, resume_only_gaps, suggested_improvements,
        # questions_for_user, confidence ALL omitted — must default cleanly
    )
    assert review.career_gaps_observed == []
    assert review.resume_only_gaps == []
    assert review.suggested_improvements == []
    assert review.questions_for_user == []
    assert review.confidence == 0


def test_resume_review_accepts_partial_haiku_shape():
    """The exact shape that crashed in production: Haiku returned overall_fit
    and critical_gaps but omitted career_gaps_observed, suggested_improvements,
    questions_for_user, confidence. Must validate cleanly now."""
    payload = {
        "job_id": "95352ddb-a2d5",
        "resume_id": "res_003",
        "overall_fit_summary": "Direct relevance to ClickUp.",
        "section_reviews": [],
        "critical_gaps": ["No formal product management title"],
        "resume_only_gaps": ["Cross-functional leadership not surfaced"],
        # career_gaps_observed, suggested_improvements, questions_for_user,
        # confidence — all missing, as in the failing run
    }
    review = ResumeReview.model_validate(payload)
    assert review.overall_fit_summary == "Direct relevance to ClickUp."
    assert review.critical_gaps == ["No formal product management title"]
    assert review.career_gaps_observed == []  # tolerated
    assert review.confidence == 0              # tolerated


def test_resume_review_still_rejects_load_bearing_omissions():
    """Tolerance is strictly limited. Removing a load-bearing field still fails."""
    import pytest as _pytest
    with _pytest.raises(Exception):
        ResumeReview.model_validate({
            "job_id": "x", "resume_id": "y",
            # overall_fit_summary missing -> must reject
            "section_reviews": [], "critical_gaps": [],
        })


# ─── TailoredBullet ──────────────────────────────────────────────────────────

def test_tailored_bullet_requires_supporting_evidence():
    with pytest.raises(ValidationError):
        TailoredBullet(
            original_text="Led team",
            suggested_text="Led team of 8",
            claim_type="reword",
            fidelity_risk="low",
            # supporting_evidence missing
        )


def test_tailored_bullet_rejects_empty_supporting_evidence():
    """A claim with empty evidence is a malformed object, not a weak suggestion.
    Enforces the 'no evidence, no claim' invariant at the schema boundary rather
    than leaving the Fidelity Reviewer as the only line of defense."""
    with pytest.raises(ValidationError):
        TailoredBullet(
            original_text="Led team",
            suggested_text="Led cross-functional team of 8 engineers",
            supporting_evidence="",  # empty -> must reject
            claim_type="reword",
            fidelity_risk="low",
        )


def test_tailored_bullet_valid():
    b = TailoredBullet(
        original_text="Led team",
        suggested_text="Led cross-functional team of 8 engineers",
        supporting_evidence="Resume states 'led team of 8' in role at Acme",
        claim_type="reword",
        fidelity_risk="low",
    )
    assert b.unsupported_claims == []


def test_tailored_bullet_gap_type():
    b = TailoredBullet(
        original_text="",
        suggested_text="Experience with Kubernetes",
        supporting_evidence="No Kubernetes experience found — this is a gap",
        claim_type="gap",
        fidelity_risk="high",
        unsupported_claims=["Kubernetes not evidenced in resume"],
    )
    assert b.claim_type == "gap"


def test_tailored_resume_draft_summary_fields_optional():
    """Claude sometimes omits the narrative-summary fields (skills_section_suggestions,
    overall_tailoring_notes, fidelity_risk_summary) — those are tolerant by design.
    Per-bullet fidelity fields stay required and are tested separately above.
    Regression for the 'tailoring timed out' bug in 2026-05-02."""
    draft = TailoredResumeDraft(
        job_id="job-1",
        resume_id="res-1",
        experience_bullet_suggestions=[
            TailoredBullet(
                original_text="Led platform team.",
                suggested_text="Led platform team owning Python services for 10M+ users.",
                supporting_evidence="Resume states 'led platform team for 3 years'.",
                claim_type="emphasize",
                fidelity_risk="low",
            ),
        ],
        # skills_section_suggestions, overall_tailoring_notes,
        # fidelity_risk_summary, summary_suggestions all omitted on purpose
    )
    assert draft.skills_section_suggestions == []
    assert draft.summary_suggestions == []
    assert draft.overall_tailoring_notes == ""
    assert draft.fidelity_risk_summary == ""


# ─── FidelityReview ──────────────────────────────────────────────────────────

def test_fidelity_review_valid():
    fr = FidelityReview(
        job_id="job_001", resume_id="res_001",
        overall_fidelity_status="pass",
        unsupported_claims=[], fabricated_metrics=[],
        inflated_scope_flags=[], unsupported_technology_flags=[],
        unsupported_certification_flags=[],
        required_removals=[], required_revisions=[],
        approval_recommendation="approve",
        confidence=95,
    )
    assert fr.approval_recommendation == "approve"


def test_fidelity_review_rejects_invalid_recommendation():
    with pytest.raises(ValidationError):
        FidelityReview(
            job_id="j", resume_id="r",
            overall_fidelity_status="pass",
            unsupported_claims=[], fabricated_metrics=[],
            inflated_scope_flags=[], unsupported_technology_flags=[],
            unsupported_certification_flags=[],
            required_removals=[], required_revisions=[],
            approval_recommendation="maybe",  # invalid
            confidence=80,
        )
