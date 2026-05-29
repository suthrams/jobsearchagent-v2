"""Tailoring router — on-demand resume tailoring for a specific (workflow, job).

Decoupled from the LangGraph workflow: callers pick a job from a completed (or
in-flight) run and trigger TailoringAgent + FidelityReviewer directly. The
draft is persisted to tailored_resumes and the user records an
approve / revise / reject decision via a separate endpoint.

URL convention:
  - Workflow-scoped routes for CREATE (POST) and LIST (GET) — tailorings always
    belong to a workflow at creation time.
  - Top-level routes (`/tailorings/{id}`) for READ-ONE and DECISION — the
    tailoring_id is a globally unique UUID, so workflow scope is redundant
    once you have the ID. This matches GitHub's pattern (`/repos/.../issues`
    for list, `/issues/{id}` for fetch via global ID).

Endpoints:
  POST /workflows/{workflow_id}/jobs/{job_id}/tailorings   - run tailoring + fidelity, persist draft
  GET  /workflows/{workflow_id}/tailorings                  - list drafts for a workflow
  GET  /tailorings/{tailoring_id}                           - fetch one draft by ID
  POST /tailorings/{tailoring_id}/decisions                 - record approve / revise / reject
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.api.decision_validation import DecisionRequest as TailoringDecisionRequest
from app.api.dependencies import get_deps, get_graph
from app.api.schemas.responses import TailoringListResponse, TailoringResponse
from app.providers.llm_client import LLMProviderError
from app.services.context_trimmer import (
    trim_career_advice,
    trim_resume_profile,
    trim_review,
    trim_score,
)
from app.services.deep_review_runner import review_one_job
from app.workflows.workflow_graph import WorkflowDependencies

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tailoring"])


# TailoringDecisionRequest is now an alias for the shared DecisionRequest in
# app/api/decision_validation.py (extracted in ADR-066 Phase 4 so the
# resume-clinic router reuses the same approve/revise/reject/edit shape with
# identical validation). Wire shape unchanged.


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_workflow_state(graph, workflow_id: str) -> dict:
    """Return the latest snapshot of a workflow's state from the checkpointer.

    Raises 404 if the workflow_id is unknown.
    """
    config = {"configurable": {"thread_id": workflow_id}}
    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "workflow_not_found",
                "message": f"Workflow {workflow_id!r} not found.",
                "workflow_id": workflow_id,
            },
        )
    return snapshot.values


def _find_job(state: dict, job_id: str, deps: WorkflowDependencies) -> dict:
    """Locate the job dict for tailoring context.

    Preference order: selected_jobs (has full description) → scored_jobs → jobs table.
    """
    for jobs_field in ("selected_jobs", "scored_jobs", "normalized_jobs", "raw_jobs"):
        for j in state.get(jobs_field) or []:
            jid = j.get("job_id") or j.get("id")
            if jid == job_id and j.get("job_description"):
                return j

    job_row = deps.job_repo.get_by_id(job_id)
    if job_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "job_not_found",
                "message": f"Job {job_id!r} not found in workflow or jobs table.",
                "job_id": job_id,
            },
        )
    return {
        "job_id": job_row["id"],
        "job_description": job_row.get("job_description") or "",
        "title": job_row.get("title"),
        "company": job_row.get("company"),
    }


def _score_by_job(state: dict) -> dict[str, dict]:
    """Map job_id -> scored-job dict from workflow state (for deep-review context)."""
    out: dict[str, dict] = {}
    for sj in state.get("scored_jobs") or []:
        jid = sj.get("job_id") or sj.get("id")
        if jid:
            out[jid] = sj
    return out


def _serialize_row(row: dict) -> TailoringResponse:
    """Trim TailoringRepository row to the API response shape (typed)."""
    return TailoringResponse(
        tailoring_id=row["id"],
        workflow_id=row["workflow_run_id"],
        job_id=row["job_id"],
        resume_id=row["resume_id"],
        tailored=row.get("tailored"),
        fidelity_review=row.get("fidelity_review"),
        decision=row.get("decision"),
        approved=bool(row.get("approved")),
        edited=row.get("edited"),
        decided_at=row.get("decided_at"),
        created_at=row.get("created_at"),
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/workflows/{workflow_id}/jobs/{job_id}/tailorings",
    status_code=200,
    response_model=TailoringResponse,
)
def trigger_tailoring(
    workflow_id: str,
    job_id: str,
    auto_deep_review: bool = True,
    graph=Depends(get_graph),
    deps: WorkflowDependencies = Depends(get_deps),
) -> TailoringResponse:
    """Run TailoringAgent + FidelityReviewer for one job, persist the draft, return it.

    Synchronous: tailoring + fidelity together typically take 5-15s end to end.
    Repeated calls produce additional drafts (caller decides whether to use the
    latest via GET /workflows/{id}/tailorings).

    ADR-061: any scored job can be tailored, not only the auto-selected top-3.
    If the job has no deep-review row yet and `auto_deep_review` is true (default),
    the ResumeCritic+ReviewAuditor reflection loop runs for this one job first so
    the TailoringAgent gets real critic context. Set `auto_deep_review=false` to
    tailor with empty review context (cheaper, lower-quality).
    """
    state = _read_workflow_state(graph, workflow_id)

    resume_id = state.get("resume_id") or ""
    resume_profile = state.get("resume_profile") or {}
    if not resume_profile:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "resume_profile_missing",
                "message": (
                    "Workflow has no parsed resume_profile yet — wait for the run to "
                    "reach load_resume before requesting tailoring."
                ),
                "workflow_id": workflow_id,
            },
        )

    job = _find_job(state, job_id, deps)

    # Pull per-job context from the persisted repos (state only carries the latest).
    review_row = deps.review_repo.get_review_by_run_job(workflow_id, job_id)

    # ADR-061: deep-review-on-demand. A scored job that was never auto-selected
    # has no critic/auditor context; run the reflection loop for it now so the
    # tailoring agent isn't working blind.
    if review_row is None and auto_deep_review:
        try:
            review_one_job(
                job={**job, "job_id": job_id},
                workflow_id=workflow_id,
                resume_id=resume_id,
                resume_profile=resume_profile,
                job_score=_score_by_job(state).get(job_id, {}),
                resume_critic=deps.resume_critic,
                review_auditor=deps.review_auditor,
                review_repo=deps.review_repo,
            )
        except LLMProviderError as exc:
            # Don't block tailoring on a failed on-demand review — proceed with
            # whatever context exists (possibly empty).
            logger.warning("trigger_tailoring: on-demand deep review failed for %s/%s: %s",
                           workflow_id, job_id, exc)
        review_row = deps.review_repo.get_review_by_run_job(workflow_id, job_id)

    advice_row = deps.advice_repo.get_advice_by_run_job(workflow_id, job_id)

    final_review: dict = {}
    if review_row and review_row.get("review_json"):
        import json
        try:
            final_review = json.loads(review_row["review_json"])
        except Exception:
            final_review = {}

    career_advice: dict = {}
    if advice_row and advice_row.get("advice_json"):
        import json
        try:
            career_advice = json.loads(advice_row["advice_json"])
        except Exception:
            career_advice = {}

    # Trim context to drop fields the tailoring agent doesn't read (raw_text,
    # critic's per-section reviews, etc.). See app/services/context_trimmer.py
    # for the per-field justification. Quality-neutral cost cut.
    #
    # resume_profile is moved into "_cached" so PromptLoader puts it in a
    # second cached system block (constant across all tailoring calls in the
    # session — gets the 10% rate on subsequent calls within the 5-min window).
    context = {
        "_cached": {
            "resume_profile": trim_resume_profile(resume_profile),
        },
        "job_id": job_id,
        "resume_id": resume_id,
        "job_description": job.get("job_description", ""),
        "final_review": trim_review(final_review),
        "career_advice": trim_career_advice(career_advice),
    }

    try:
        draft = deps.tailoring_agent.run(workflow_id, context)
    except LLMProviderError as exc:
        logger.warning("trigger_tailoring: TailoringAgent failed for %s/%s: %s",
                       workflow_id, job_id, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "tailoring_failed",
                "message": str(exc),
                "workflow_id": workflow_id,
                "job_id": job_id,
            },
        ) from exc

    draft_dict = draft.model_dump() if hasattr(draft, "model_dump") else dict(draft)

    try:
        # Cache the resume_profile: the tailoring call right above already
        # warmed the same cache key (it uses trim_resume_profile in `_cached`
        # too), so this fidelity call should read it back at 10% rate within
        # the 5-min window.
        fidelity = deps.fidelity_reviewer.run(workflow_id, {
            "_cached": {
                "resume_profile": trim_resume_profile(resume_profile),
            },
            "job_id": job_id,
            "resume_id": resume_id,
            "job_description": job.get("job_description", ""),
            "tailored_draft": draft_dict,
        })
        fidelity_dict = fidelity.model_dump() if hasattr(fidelity, "model_dump") else dict(fidelity)
    except LLMProviderError as exc:
        logger.warning("trigger_tailoring: FidelityReviewer failed for %s/%s: %s",
                       workflow_id, job_id, exc)
        # Persist the draft anyway so the user can see it, but flag fidelity as unavailable.
        fidelity_dict = None

    tailoring_id = str(uuid.uuid4())
    deps.tailoring_repo.create(
        tailoring_id, workflow_id, job_id, resume_id, draft_dict,
        fidelity_review=fidelity_dict,
    )

    row = deps.tailoring_repo.get_by_id(tailoring_id) or {
        "id": tailoring_id,
        "workflow_run_id": workflow_id,
        "job_id": job_id,
        "resume_id": resume_id,
        "tailored": draft_dict,
        "fidelity_review": fidelity_dict,
        "decision": None,
        "approved": False,
        "decided_at": None,
        "created_at": None,
    }
    return _serialize_row(row)


@router.get("/workflows/{workflow_id}/tailorings", response_model=TailoringListResponse)
def list_tailorings(
    workflow_id: str,
    deps: WorkflowDependencies = Depends(get_deps),
) -> TailoringListResponse:
    rows = deps.tailoring_repo.list_by_workflow(workflow_id)
    return TailoringListResponse(
        workflow_id=workflow_id,
        tailorings=[_serialize_row(r) for r in rows],
    )


@router.get("/tailorings/{tailoring_id}", response_model=TailoringResponse)
def get_tailoring(
    tailoring_id: str,
    deps: WorkflowDependencies = Depends(get_deps),
) -> TailoringResponse:
    row = deps.tailoring_repo.get_by_id(tailoring_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "tailoring_not_found",
                "message": f"Tailoring {tailoring_id!r} not found.",
                "tailoring_id": tailoring_id,
            },
        )
    return _serialize_row(row)


@router.post("/tailorings/{tailoring_id}/decisions", status_code=200,
             response_model=TailoringResponse)
def submit_tailoring_decision(
    tailoring_id: str,
    body: TailoringDecisionRequest,
    deps: WorkflowDependencies = Depends(get_deps),
) -> TailoringResponse:
    row = deps.tailoring_repo.get_by_id(tailoring_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "tailoring_not_found",
                "message": f"Tailoring {tailoring_id!r} not found.",
                "tailoring_id": tailoring_id,
            },
        )
    deps.tailoring_repo.set_decision(tailoring_id, body.approval, edited=body.edited)
    updated = deps.tailoring_repo.get_by_id(tailoring_id)
    return _serialize_row(updated or row)


# ── On-demand deep review (ADR-061) ───────────────────────────────────────────

@router.post("/workflows/{workflow_id}/jobs/{job_id}/deep-review", status_code=200)
def trigger_deep_review(
    workflow_id: str,
    job_id: str,
    graph=Depends(get_graph),
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """Run the ResumeCritic+ReviewAuditor reflection loop for one scored job.

    Out-of-graph, synchronous (ADR-061). Lets a user push any scored job through
    deep review without it having been one of the auto-selected top-3. Persists
    rounds + the final review via ReviewRepository and returns the best review.
    """
    state = _read_workflow_state(graph, workflow_id)
    resume_id = state.get("resume_id") or ""
    resume_profile = state.get("resume_profile") or {}
    if not resume_profile:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "resume_profile_missing",
                "message": (
                    "Workflow has no parsed resume_profile yet — wait for the run "
                    "to reach load_resume before requesting deep review."
                ),
                "workflow_id": workflow_id,
            },
        )

    job = _find_job(state, job_id, deps)

    try:
        (_jid, rounds, best_review, errs, calls, _ti, _to, _cost) = review_one_job(
            job={**job, "job_id": job_id},
            workflow_id=workflow_id,
            resume_id=resume_id,
            resume_profile=resume_profile,
            job_score=_score_by_job(state).get(job_id, {}),
            resume_critic=deps.resume_critic,
            review_auditor=deps.review_auditor,
            review_repo=deps.review_repo,
        )
    except LLMProviderError as exc:
        logger.warning("trigger_deep_review: failed for %s/%s: %s",
                       workflow_id, job_id, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "deep_review_failed",
                "message": str(exc),
                "workflow_id": workflow_id,
                "job_id": job_id,
            },
        ) from exc

    return {
        "workflow_id": workflow_id,
        "job_id": job_id,
        "review": best_review,
        "rounds": len(rounds),
        "llm_calls": calls,
        "errors": errs,
    }


# ── On-demand interview prep (ADR-061) ─────────────────────────────────────────

@router.post("/workflows/{workflow_id}/jobs/{job_id}/interview-prep", status_code=200)
def trigger_interview_prep(
    workflow_id: str,
    job_id: str,
    graph=Depends(get_graph),
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    """Run the InterviewCoach for one chosen scored job, persist, and return it.

    Out-of-graph, synchronous (ADR-061). Mirrors the interview_prep node but for
    a single user-chosen job, regardless of threshold. Career-advice and
    final-review context are sourced from the repos (as the tailoring endpoint
    does); the prep is persisted via AdviceRepository.create_prep.
    """
    import json
    import uuid as _uuid

    state = _read_workflow_state(graph, workflow_id)
    resume_profile = state.get("resume_profile") or {}
    if not resume_profile:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "resume_profile_missing",
                "message": (
                    "Workflow has no parsed resume_profile yet — wait for the run "
                    "to reach load_resume before requesting interview prep."
                ),
                "workflow_id": workflow_id,
            },
        )

    job = _find_job(state, job_id, deps)
    job_score = _score_by_job(state).get(job_id, {})

    # Repo-sourced context (state only carries the latest job's review/advice).
    review_row = deps.review_repo.get_review_by_run_job(workflow_id, job_id)
    advice_row = deps.advice_repo.get_advice_by_run_job(workflow_id, job_id)
    final_review: dict = {}
    if review_row and review_row.get("review_json"):
        try:
            final_review = json.loads(review_row["review_json"])
        except Exception:
            final_review = {}
    career_advice: dict = {}
    if advice_row and advice_row.get("advice_json"):
        try:
            career_advice = json.loads(advice_row["advice_json"])
        except Exception:
            career_advice = {}

    try:
        prep = deps.interview_coach.run(workflow_id, {
            "_cached": {"resume_profile": trim_resume_profile(resume_profile)},
            "job_id": job_id,
            "job_description": job.get("job_description", ""),
            "job_score": trim_score(job_score),
            "research_context": {},
            "career_advice": trim_career_advice(career_advice),
            "final_review": trim_review(final_review),
        })
    except LLMProviderError as exc:
        logger.warning("trigger_interview_prep: failed for %s/%s: %s",
                       workflow_id, job_id, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "interview_prep_failed",
                "message": str(exc),
                "workflow_id": workflow_id,
                "job_id": job_id,
            },
        ) from exc

    prep_dict = prep.model_dump() if hasattr(prep, "model_dump") else dict(prep)
    try:
        deps.advice_repo.create_prep(str(_uuid.uuid4()), workflow_id, job_id, prep_dict)
    except Exception as exc:
        logger.warning("trigger_interview_prep: persist failed for %s/%s: %s",
                       workflow_id, job_id, exc)

    return {
        "workflow_id": workflow_id,
        "job_id": job_id,
        "prep": prep_dict,
    }
