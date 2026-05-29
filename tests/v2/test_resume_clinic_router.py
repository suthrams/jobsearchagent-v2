"""Integration tests for the Resume Clinic router (ADR-066 Phase 4).

Exercises:
  POST /users/{id}/resume-clinic
  GET  /users/{id}/resume-clinic
  POST /resume-clinic/{review_id}/decisions

Uses dependency overrides so no real LangGraph, ConfigService, or DB is touched.
Mocks the runner inputs (resume repo + clinic repo + reviewer + fidelity) and
asserts wiring + decision flow. The runner itself is unit-tested in
test_resume_clinic_runner.py; this file focuses on the HTTP surface.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_deps, get_graph
from app.api.main import app
from app.repositories.database import utcnow_iso
from app.schemas.fidelity_review import FidelityReview
from app.schemas.resume_chat import ResumeChatTurnResult
from app.schemas.resume_clinic import ResumeClinicReview
from app.workflows.workflow_graph import WorkflowDependencies


USER_ID = "0"
RESUME_ID = "res-clinic-001"
OTHER_RESUME_ID = "res-clinic-other"


def _review() -> ResumeClinicReview:
    return ResumeClinicReview(
        quality={
            "dimensions": [
                {"dimension": "structure_ordering", "rating": "adequate",
                 "findings": ["projects buried"], "fixes": ["promote projects"]},
            ],
            "overall_summary": "Solid foundation; quantification and reorder would help.",
        },
        alignment={
            "fit_summary": "moderate fit",
            "missing_skills": ["AWS"],
            "missing_keywords": [],
            "suggested_certifications": [],
            "suggested_projects": [],
            "emphasize": [],
            "confidence": "medium",
        },
        reorganization={"section_order": ["summary", "experience"], "moves": []},
        rewrites=[
            {
                "section_label": "experience:Acme:Engineer",
                "original_text": "Worked on backend systems.",
                "suggested_text": "Designed and shipped a backend service handling 200 RPS.",
                "claim_type": "quantify",
                "supporting_evidence": "Resume mentions backend role.",
            },
        ],
    )


def _fidelity() -> FidelityReview:
    return FidelityReview(
        job_id="clinic:x",
        resume_id=RESUME_ID,
        overall_fidelity_status="pass",
        unsupported_claims=[],
        fabricated_metrics=[],
        inflated_scope_flags=[],
        unsupported_technology_flags=[],
        unsupported_certification_flags=[],
        required_removals=[],
        required_revisions=[],
        approval_recommendation="approve",
        confidence=90,
    )


def _make_resume_row(user_id=USER_ID, resume_id=RESUME_ID):
    return {
        "id": resume_id,
        "user_id": user_id,
        "raw_text": "Software engineer with 5 years experience.",
        "parsed_profile_json": json.dumps({
            "name": "Test User",
            "headline": "Software Engineer",
            "email": "test@example.com",
            "summary": "Backend engineer.",
            "skills": ["Python"],
            "experience": [
                {
                    "company": "Acme",
                    "title": "Engineer",
                    "start_year": 2022,
                    "end_year": None,
                    "description": "Worked on backend systems.",
                    "technologies": ["Python"],
                },
            ],
        }),
    }


def _chat_turn_result() -> ResumeChatTurnResult:
    return ResumeChatTurnResult(
        reply="Trimmed the summary and tightened the closing.",
        overhaul={
            "reorganization": {"section_order": ["summary", "experience"], "moves": []},
            "rewrites": [{
                "section_label": "experience:Acme:Engineer",
                "original_text": "Worked on backend systems.",
                "suggested_text": "CHAT EDIT: shipped backend services at scale.",
                "claim_type": "restate",
                "supporting_evidence": "Resume mentions a backend role.",
            }],
        },
        changed_sections=["experience"],
    )


def _make_deps() -> WorkflowDependencies:
    # Reviewer + fidelity agents (mocked)
    reviewer = MagicMock()
    reviewer.run.return_value = _review()
    fidelity = MagicMock()
    fidelity.run.return_value = _fidelity()

    chat = MagicMock()
    chat.run.return_value = _chat_turn_result()

    # Resume repo with two resumes (one owned by USER_ID, one orphaned to "7")
    resumes = {
        RESUME_ID: _make_resume_row(),
        OTHER_RESUME_ID: _make_resume_row(user_id="7", resume_id=OTHER_RESUME_ID),
    }
    resume_repo = MagicMock()
    resume_repo.get_by_id.side_effect = lambda rid: resumes.get(rid)
    resume_repo.get_active.side_effect = lambda uid: (
        resumes[RESUME_ID] if str(uid) == USER_ID else None
    )

    # In-memory clinic repo so the router can persist and read back.
    clinic_store: dict[str, dict] = {}

    def _clinic_create(clinic_id, user_id, resume_id, *, workflow_run_id,
                       target_role, target_track, seniority_aware,
                       review, alignment, overhaul, fidelity_review):
        clinic_store[clinic_id] = {
            "id": clinic_id,
            "user_id": user_id,
            "resume_id": resume_id,
            "workflow_run_id": workflow_run_id,
            "target_role": target_role,
            "target_track": target_track,
            "seniority_aware": bool(seniority_aware),
            "review": review,
            "alignment": alignment,
            "overhaul": overhaul,
            "fidelity_review": fidelity_review,
            "decision": None,
            "edited": None,
            "decided_at": None,
            "created_at": utcnow_iso(),
        }

    def _clinic_get(clinic_id):
        return clinic_store.get(clinic_id)

    def _clinic_list(user_id):
        return [r for r in clinic_store.values() if r["user_id"] == str(user_id)]

    def _clinic_decision(clinic_id, decision, edited=None):
        if clinic_id in clinic_store:
            clinic_store[clinic_id]["decision"] = decision
            clinic_store[clinic_id]["decided_at"] = utcnow_iso()
            clinic_store[clinic_id]["edited"] = edited

    def _clinic_set_edited(clinic_id, edited, fidelity_review=None):
        if clinic_id in clinic_store:
            clinic_store[clinic_id]["edited"] = edited
            clinic_store[clinic_id]["fidelity_review"] = fidelity_review

    def _clinic_discard_edits(clinic_id):
        if clinic_id in clinic_store:
            clinic_store[clinic_id]["edited"] = None
            clinic_store[clinic_id]["decision"] = None
            clinic_store[clinic_id]["decided_at"] = None

    clinic_repo = MagicMock()
    clinic_repo.create.side_effect = _clinic_create
    clinic_repo.get_by_id.side_effect = _clinic_get
    clinic_repo.list_by_user.side_effect = _clinic_list
    clinic_repo.set_decision.side_effect = _clinic_decision
    clinic_repo.set_edited.side_effect = _clinic_set_edited
    clinic_repo.discard_edits.side_effect = _clinic_discard_edits

    # Workflow repo: no-op create/update; get_by_status returns empty.
    workflow_repo = MagicMock()
    workflow_repo.create.return_value = None
    workflow_repo.update_state.return_value = None
    workflow_repo.get_by_status.return_value = []
    workflow_repo.get_by_id.return_value = None

    # In-memory observability so log_llm_call / get_llm_calls_by_run roundtrip.
    # This is what the new cost-cap + session-cost-rollup logic reads through.
    llm_call_log: list[dict] = []

    def _log_llm_call(*args, workflow_id, agent_name, provider, model,
                      tokens_input, tokens_output, cost_usd, latency_ms,
                      cache_creation_tokens=0, cache_read_tokens=0, **kwargs):
        llm_call_log.append({
            "workflow_run_id": workflow_id,
            "agent_name": agent_name,
            "provider": provider,
            "model": model,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "estimated_cost": cost_usd,
            "latency_ms": latency_ms,
        })

    def _get_llm_calls_by_run(workflow_id):
        return [r for r in llm_call_log if r["workflow_run_id"] == workflow_id]

    observability = MagicMock()
    observability.log_llm_call.side_effect = _log_llm_call
    observability.get_llm_calls_by_run.side_effect = _get_llm_calls_by_run
    # Expose the log so tests can also seed rows directly (e.g. to drive the
    # cap into the 429 branch without going through 25 real round-trips).
    observability.llm_call_log = llm_call_log

    return WorkflowDependencies(
        research_agent=MagicMock(),
        scoring_agent=MagicMock(),
        resume_critic=MagicMock(),
        review_auditor=MagicMock(),
        career_advisor=MagicMock(),
        interview_coach=MagicMock(),
        tailoring_agent=MagicMock(),
        fidelity_reviewer=fidelity,
        resume_reviewer=reviewer,
        resume_chat=chat,
        discovery_service=MagicMock(),
        resume_parser=MagicMock(),
        report_generator=MagicMock(),
        job_repo=MagicMock(),
        score_repo=MagicMock(),
        advice_repo=MagicMock(),
        review_repo=MagicMock(),
        tailoring_repo=MagicMock(),
        resume_clinic_repo=clinic_repo,
        workflow_repo=workflow_repo,
        resume_repo=resume_repo,
        observability=observability,
        checkpointer=MagicMock(),
    )


@pytest.fixture
def client():
    """Pre-built deps so each test can read the same in-memory clinic_store
    via the bound side-effects. Graph dependency is overridden to a MagicMock."""
    deps = _make_deps()
    app.dependency_overrides[get_deps] = lambda: deps
    app.dependency_overrides[get_graph] = lambda: MagicMock()
    yield TestClient(app), deps
    app.dependency_overrides.clear()


# ── POST /users/{id}/resume-clinic ──────────────────────────────────────────

def test_post_clinic_success_with_target(client):
    c, _ = client
    resp = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={
            "resume_id": RESUME_ID,
            "target_role": "security analyst",
            "target_track": "ic",
            "seniority_aware": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clinic_id"]
    assert body["user_id"] == USER_ID
    assert body["resume_id"] == RESUME_ID
    assert body["target_role"] == "security analyst"
    assert body["target_track"] == "ic"
    assert body["seniority_aware"] is True
    assert body["quality"]["overall_summary"].startswith("Solid")
    assert body["alignment"]["confidence"] == "medium"
    assert body["overhaul"]["rewrites"][0]["claim_type"] == "quantify"
    assert body["fidelity_review"]["approval_recommendation"] == "approve"


def test_post_clinic_quality_only_when_no_target(client):
    c, deps = client
    # Reviewer that returns alignment=None
    deps.resume_reviewer.run.return_value = ResumeClinicReview(
        quality={
            "dimensions": [
                {"dimension": "clarity", "rating": "strong",
                 "findings": [], "fixes": []},
            ],
            "overall_summary": "Clean.",
        },
        alignment=None,
        reorganization={"section_order": ["summary"], "moves": []},
        rewrites=[],
    )
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": RESUME_ID,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_role"] is None
    assert body["target_track"] is None
    assert body["alignment"] is None


def test_post_clinic_uses_active_resume_when_resume_id_omitted(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["resume_id"] == RESUME_ID


def test_post_clinic_404_unknown_resume(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": "does-not-exist",
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "resume_not_found"


def test_post_clinic_404_when_using_other_users_resume(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": OTHER_RESUME_ID,
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "resume_not_found"


def test_post_clinic_422_invalid_target_track(client):
    c, _ = client
    resp = c.post(f"/users/{USER_ID}/resume-clinic", json={
        "resume_id": RESUME_ID,
        "target_track": "sales",
    })
    assert resp.status_code == 422
    # Our main.py normalizes Pydantic validation errors to {error, message, details}.
    detail = resp.json()["detail"]
    assert detail["error"] == "validation_error"


# ── GET /users/{id}/resume-clinic ────────────────────────────────────────────

def test_get_clinic_lists_user_runs_newest_first(client):
    c, _ = client
    # Seed two runs.
    c.post(f"/users/{USER_ID}/resume-clinic", json={"resume_id": RESUME_ID})
    c.post(f"/users/{USER_ID}/resume-clinic", json={"resume_id": RESUME_ID})
    resp = c.get(f"/users/{USER_ID}/resume-clinic")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == USER_ID
    assert len(body["reviews"]) == 2


def test_get_clinic_empty_for_user_with_no_runs(client):
    c, _ = client
    resp = c.get(f"/users/{USER_ID}/resume-clinic")
    assert resp.status_code == 200, resp.text
    assert resp.json()["reviews"] == []


# ── POST /resume-clinic/{id}/decisions ──────────────────────────────────────

def test_decision_approve_records_state(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(f"/resume-clinic/{clinic_id}/decisions",
                  json={"approval": "approve"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["decided_at"] is not None
    assert body["edited"] is None


def test_decision_edit_persists_edited(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    edited_payload = {"reorganization": {"section_order": ["x"], "moves": []}, "rewrites": []}
    resp = c.post(
        f"/resume-clinic/{clinic_id}/decisions",
        json={"approval": "edit", "edited": edited_payload},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "edit"
    assert body["edited"] == edited_payload


def test_decision_edit_requires_edited_payload(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(f"/resume-clinic/{clinic_id}/decisions",
                  json={"approval": "edit"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "validation_error"


def test_decision_404_when_review_unknown(client):
    c, _ = client
    resp = c.post("/resume-clinic/does-not-exist/decisions",
                  json={"approval": "approve"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "clinic_review_not_found"


def test_decision_invalid_value_422(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(f"/resume-clinic/{clinic_id}/decisions",
                  json={"approval": "maybe"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "validation_error"


# ── GET /resume-clinic/{id}/export ──────────────────────────────────────────

import pytest


@pytest.mark.parametrize("fmt,name_token,content_type_substring,magic_bytes", [
    # name_token=None means "skip the inline name check" - useful for binary
    # formats where the name is inside a compressed container (DOCX). The
    # renderer-layer tests already unzip and confirm.
    ("md",   b"Test User",   "text/markdown",                       None),
    ("txt",  b"TEST USER",   "text/plain",                          None),
    ("html", b"Test User",   "text/html",                           b"<!DOCTYPE"),
    ("json", b"Test User",   "application/json",                    b"{"),
    ("docx", None,           "wordprocessingml.document",           b"PK\x03\x04"),
    ("pdf",  b"Test User",   "application/pdf",                     b"%PDF-"),
])
def test_export_returns_each_format(client, fmt, name_token, content_type_substring,
                                    magic_bytes):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    # Approve the review so the export is the canonical "decision applied" path
    # without the preview banner getting in the way of the content checks.
    c.post(f"/resume-clinic/{clinic_id}/decisions", json={"approval": "approve"})

    resp = c.get(f"/resume-clinic/{clinic_id}/export?format={fmt}")
    assert resp.status_code == 200, resp.text
    if name_token is not None:
        assert name_token in resp.content, (
            f"format={fmt} did not include the candidate name in the output"
        )
    if magic_bytes is not None:
        assert resp.content.startswith(magic_bytes), (
            f"format={fmt} produced unexpected prefix: {resp.content[:32]!r}"
        )
    assert content_type_substring in resp.headers["content-type"]
    # Download-friendly disposition header always set.
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert clinic_id[:8] in cd


def test_export_unknown_format_400(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.get(f"/resume-clinic/{clinic_id}/export?format=xyz")
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unsupported_format"


def test_export_unknown_review_404(client):
    c, _ = client
    resp = c.get("/resume-clinic/does-not-exist/export?format=md")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "clinic_review_not_found"


def test_export_default_format_is_markdown(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.get(f"/resume-clinic/{clinic_id}/export")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]


def test_export_respects_decision_reject_renders_original_only(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    c.post(f"/resume-clinic/{clinic_id}/decisions", json={"approval": "reject"})

    resp = c.get(f"/resume-clinic/{clinic_id}/export?format=md")
    assert resp.status_code == 200
    md = resp.content.decode("utf-8")
    # The mock review's rewrite was "200 RPS" - on reject it should NOT appear,
    # because the original parsed_profile in the test fixture has no such bullet.
    assert "200 RPS" not in md


def test_export_respects_decision_approve_applies_overhaul(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    c.post(f"/resume-clinic/{clinic_id}/decisions", json={"approval": "approve"})

    resp = c.get(f"/resume-clinic/{clinic_id}/export?format=md")
    assert resp.status_code == 200
    md = resp.content.decode("utf-8")
    # The mock overhaul contains "200 RPS" - on approve it should land in the
    # rendered output (the rewrite gets appended even with no matching bullet).
    assert "200 RPS" in md


# ── ADR-068: POST /resume-clinic/{id}/chat ──────────────────────────────────


def test_chat_round_trip_persists_edited_and_returns_reply(client):
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(
        f"/resume-clinic/{clinic_id}/chat",
        json={"message": "make the experience section sharper",
              "section": "experience"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"].startswith("Trimmed")
    assert body["changed_sections"] == ["experience"]
    # The persisted clinic row now has edited_json populated, decision unchanged.
    row = deps.resume_clinic_repo.get_by_id(clinic_id)
    assert row["edited"] is not None
    assert any("CHAT EDIT" in r["suggested_text"]
               for r in row["edited"]["rewrites"])
    assert row["decision"] is None


def test_chat_always_runs_fidelity_when_rewrites_exist(client):
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(
        f"/resume-clinic/{clinic_id}/chat",
        json={"message": "tighten the summary"},
    )
    assert resp.status_code == 200, resp.text
    # The mocked fidelity agent was called.
    deps.fidelity_reviewer.run.assert_called()
    assert resp.json()["fidelity_review"] is not None


def test_chat_persists_null_fidelity_when_reviewer_raises(client):
    from app.providers.llm_client import LLMProviderError

    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    deps.fidelity_reviewer.run.side_effect = LLMProviderError("upstream")
    resp = c.post(
        f"/resume-clinic/{clinic_id}/chat",
        json={"message": "tighten the summary"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fidelity_review"] is None
    # The edited overhaul was still persisted - the user still got the revision.
    row = deps.resume_clinic_repo.get_by_id(clinic_id)
    assert row["edited"] is not None


def test_chat_uses_edited_when_present_else_overhaul_as_input(client):
    """Second turn input should be the edited state from the first turn,
    not the original agent overhaul."""
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "first turn"})
    # On the second turn, capture what current_overhaul was passed to the agent.
    deps.resume_chat.run.reset_mock()
    c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "second turn"})
    ctx = deps.resume_chat.run.call_args.args[1]
    # current_overhaul should be the FIRST turn's edit (which contains
    # "CHAT EDIT"), not the agent's original overhaul.
    rewrites = (ctx.get("current_overhaul") or {}).get("rewrites") or []
    assert any("CHAT EDIT" in r.get("suggested_text", "") for r in rewrites)


def test_chat_does_not_change_decision_field(client):
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    c.post(f"/resume-clinic/{clinic_id}/decisions", json={"approval": "revise"})

    c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "tweak"})
    row = deps.resume_clinic_repo.get_by_id(clinic_id)
    assert row["decision"] == "revise"  # unchanged by chat


def test_chat_does_not_send_raw_text_to_the_agent(client):
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    deps.resume_chat.run.reset_mock()
    c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "x"})
    ctx = deps.resume_chat.run.call_args.args[1]
    assert "raw_text" not in ctx


def test_chat_history_is_capped_at_10_turns(client):
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    history = [{"role": "user", "message": f"msg-{i}"} for i in range(50)]
    deps.resume_chat.run.reset_mock()
    c.post(
        f"/resume-clinic/{clinic_id}/chat",
        json={"message": "next", "history": history},
    )
    ctx = deps.resume_chat.run.call_args.args[1]
    assert len(ctx["history"]) == 10
    # The last 10 turns are kept.
    assert ctx["history"][-1]["message"] == "msg-49"


def test_chat_404_when_review_unknown(client):
    c, _ = client
    resp = c.post(
        "/resume-clinic/does-not-exist/chat",
        json={"message": "tweak"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "clinic_review_not_found"


def test_chat_422_when_message_empty(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": ""})
    assert resp.status_code == 422


def test_chat_422_when_unknown_section(client):
    c, _ = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]

    resp = c.post(
        f"/resume-clinic/{clinic_id}/chat",
        json={"message": "x", "section": "alignment"},
    )
    assert resp.status_code == 422


# ── ADR-068: POST /resume-clinic/{id}/discard-edits ─────────────────────────


def test_discard_edits_clears_edited_and_decision(client):
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    # Plant some edited state.
    c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "tweak"})
    c.post(f"/resume-clinic/{clinic_id}/decisions",
           json={"approval": "edit",
                 "edited": {"reorganization": {"section_order": [], "moves": []},
                            "rewrites": []}})
    pre = deps.resume_clinic_repo.get_by_id(clinic_id)
    assert pre["edited"] is not None
    assert pre["decision"] == "edit"

    resp = c.post(f"/resume-clinic/{clinic_id}/discard-edits")
    assert resp.status_code == 200
    assert resp.json()["cleared"] is True

    post = deps.resume_clinic_repo.get_by_id(clinic_id)
    assert post["edited"] is None
    assert post["decision"] is None


def test_discard_edits_404_when_review_unknown(client):
    c, _ = client
    resp = c.post("/resume-clinic/does-not-exist/discard-edits")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "clinic_review_not_found"


# ── ADR-068 cost monitoring (2026-05-29) ────────────────────────────────────


def _seed_llm_call(deps, *, workflow_run_id: str, agent_name: str,
                   estimated_cost: float = 0.012) -> None:
    """Directly append a row to the in-memory observability log so tests can
    exercise the cap / cost-rollup logic without going through 25+ real
    endpoint round-trips."""
    deps.observability.llm_call_log.append({
        "workflow_run_id": workflow_run_id,
        "agent_name": agent_name,
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "tokens_input": 1000,
        "tokens_output": 300,
        "estimated_cost": estimated_cost,
        "latency_ms": 1500,
    })


def _wire_agents_to_log(deps, *, chat_cost: float = 0.012,
                        fidelity_cost: float = 0.005) -> None:
    """Make the mock chat + fidelity agents call observability.log_llm_call
    when they run, mirroring what BaseAgent does in production. This is what
    the end-to-end cost-tracking invariant rides on."""
    real_chat = deps.resume_chat.run.return_value
    real_fid = deps.fidelity_reviewer.run.return_value

    def _chat_run(workflow_id, context):
        deps.observability.log_llm_call(
            workflow_id=workflow_id, agent_name="resume_chat",
            provider="claude", model="claude-sonnet-4-6",
            tokens_input=1200, tokens_output=400,
            cost_usd=chat_cost, latency_ms=1800,
        )
        return real_chat

    def _fid_run(workflow_id, context):
        deps.observability.log_llm_call(
            workflow_id=workflow_id, agent_name="fidelity_reviewer",
            provider="claude", model="claude-sonnet-4-6",
            tokens_input=600, tokens_output=150,
            cost_usd=fidelity_cost, latency_ms=900,
        )
        return real_fid

    deps.resume_chat.run.side_effect = _chat_run
    deps.fidelity_reviewer.run.side_effect = _fid_run


def test_chat_response_returns_turns_used_max_turns_and_session_cost(client):
    """End-to-end cost-tracking invariant: after a chat round-trip the
    response carries turns_used (counted from llm_calls), max_turns (the
    effective cap), and session_cost_usd (sum of estimated_cost across all
    rows tagged with the clinic's workflow_run_id)."""
    c, deps = client

    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    workflow_run_id = deps.resume_clinic_repo.get_by_id(clinic_id)["workflow_run_id"]

    # Wire AFTER the initial clinic POST so the run_clinic's own agent calls
    # don't pollute the seed - we want the seeded rows below to represent
    # the prior cumulative state, not "the initial clinic plus seed".
    _wire_agents_to_log(deps, chat_cost=0.012, fidelity_cost=0.005)

    # Seed prior rows: 1 chat (0.012) + 1 fidelity (0.005) from an earlier
    # iteration. After this turn we expect 2 chat turns total.
    _seed_llm_call(deps, workflow_run_id=workflow_run_id,
                   agent_name="resume_chat", estimated_cost=0.012)
    _seed_llm_call(deps, workflow_run_id=workflow_run_id,
                   agent_name="fidelity_reviewer", estimated_cost=0.005)

    resp = c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "tighten"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["turns_used"] == 2          # 1 prior + 1 from this turn
    assert body["max_turns"] == 25
    # 2 chat * 0.012 + 2 fidelity * 0.005 = 0.034
    assert abs(body["session_cost_usd"] - 0.034) < 1e-6


def test_chat_blocks_with_429_when_turn_cap_reached(client):
    """Pre-seed 25 chat llm_calls on the clinic's workflow_run_id; the next
    POST /chat must return 429 before any LLM call is made."""
    c, deps = client
    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    workflow_run_id = deps.resume_clinic_repo.get_by_id(clinic_id)["workflow_run_id"]

    for _ in range(25):
        _seed_llm_call(deps, workflow_run_id=workflow_run_id,
                       agent_name="resume_chat")

    # Reset agent-mock call counts so we can prove the LLM was NOT called.
    deps.resume_chat.run.reset_mock()
    deps.fidelity_reviewer.run.reset_mock()

    resp = c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "more"})
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["error"] == "chat_turn_cap_reached"
    assert detail["turns_used"] == 25
    assert detail["max_turns"] == 25
    # The cap check fires BEFORE any LLM call.
    deps.resume_chat.run.assert_not_called()
    deps.fidelity_reviewer.run.assert_not_called()


def test_chat_cap_respects_env_var_override(client, monkeypatch):
    """RESUME_CHAT_MAX_TURNS env var overrides MAX_CHAT_TURNS_PER_CLINIC."""
    c, deps = client
    monkeypatch.setenv("RESUME_CHAT_MAX_TURNS", "3")

    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    workflow_run_id = deps.resume_clinic_repo.get_by_id(clinic_id)["workflow_run_id"]

    # 3 prior chat turns; cap is 3; next call should 429.
    for _ in range(3):
        _seed_llm_call(deps, workflow_run_id=workflow_run_id,
                       agent_name="resume_chat")
    resp = c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "x"})
    assert resp.status_code == 429
    assert resp.json()["detail"]["max_turns"] == 3


def test_chat_response_max_turns_reflects_env_var(client, monkeypatch):
    """When the env var lowers the cap, the response carries the lowered
    value (so the UI's meter shows the correct ceiling)."""
    c, deps = client
    monkeypatch.setenv("RESUME_CHAT_MAX_TURNS", "10")

    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    # Wire AFTER the initial clinic POST (same reason as above).
    _wire_agents_to_log(deps)

    resp = c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "x"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_turns"] == 10
    assert resp.json()["turns_used"] == 1


def test_chat_session_cost_includes_reviewer_chat_and_fidelity_rows(client):
    """The session-cost rollup is the sum of estimated_cost across EVERY
    llm_calls row tagged with this clinic's workflow_run_id - not just chat
    rows. Verifies the rollup is end-to-end (reviewer + every chat turn +
    every fidelity call)."""
    c, deps = client

    created = c.post(
        f"/users/{USER_ID}/resume-clinic",
        json={"resume_id": RESUME_ID},
    ).json()
    clinic_id = created["clinic_id"]
    workflow_run_id = deps.resume_clinic_repo.get_by_id(clinic_id)["workflow_run_id"]

    # Wire AFTER the initial clinic POST (same reason as above).
    _wire_agents_to_log(deps, chat_cost=0.012, fidelity_cost=0.005)

    # Pretend the initial clinic spent the reviewer + 1 fidelity call.
    _seed_llm_call(deps, workflow_run_id=workflow_run_id,
                   agent_name="resume_reviewer", estimated_cost=0.080)
    _seed_llm_call(deps, workflow_run_id=workflow_run_id,
                   agent_name="fidelity_reviewer", estimated_cost=0.020)

    resp = c.post(f"/resume-clinic/{clinic_id}/chat", json={"message": "x"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # turns_used counts ONLY chat rows (1 from this turn, since prior rows
    # are reviewer + fidelity, not chat).
    assert body["turns_used"] == 1
    # session_cost includes all four rows: reviewer (0.080) + initial
    # fidelity (0.020) + this turn's chat (0.012) + this turn's fidelity (0.005).
    assert abs(body["session_cost_usd"] - 0.117) < 1e-6
