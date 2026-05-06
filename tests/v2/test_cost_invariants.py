"""System-level cost-observability invariants.

These tests exist for one reason: a bug where llm_calls and run_metrics
stayed empty in production despite ~$20 of API spend went unnoticed for
weeks because every module's unit tests passed. The unit tests confirmed
what the code DID; nothing confirmed what the system SHOULD PRODUCE.

This file codifies the system-level promises that would have caught
that class of bug. Every test runs the full per-agent or per-workflow
path against real repositories on a tmp_path SQLite DB and asserts the
audit-trail row count is what it must be for cost to be reconcilable.

If a future refactor breaks any of these assertions, cost attribution
breaks. They are non-negotiable for a system whose primary operational
concern is API spend.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.career_advisor import CareerAdvisor
from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.interview_coach import InterviewCoach
from app.agents.research_agent import ResearchAgent
from app.agents.resume_critic import ResumeCritic
from app.agents.review_auditor import ReviewAuditor
from app.agents.scoring_agent import ScoringAgent
from app.agents.tailoring_agent import TailoringAgent
from app.providers.llm_client import LLMClient, LLMUsage
from app.repositories.database import init_db, utcnow_iso
from app.repositories.decision_repository import DecisionRepository
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.security_repository import SecurityRepository
from app.repositories.step_repository import StepRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.observability_service import ObservabilityService
from app.workflows.nodes.generate_report import make_generate_report_node
from app.workflows.nodes.register_run import make_register_run_node


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path) -> Path:
    path = tmp_path / "cost_invariants.db"
    init_db(path)
    return path


@pytest.fixture
def obs_service(db_path) -> ObservabilityService:
    return ObservabilityService(
        ObservabilityRepository(db_path=db_path),
        StepRepository(db_path=db_path),
        DecisionRepository(db_path=db_path),
        SecurityRepository(db_path=db_path),
    )


def _llm_call_count(db_path: Path, workflow_id: str) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE workflow_run_id = ?",
        (workflow_id,),
    ).fetchone()[0]
    conn.close()
    return n


def _run_metrics_row(db_path: Path, workflow_id: str) -> tuple | None:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT total_llm_calls, total_tokens_input, total_tokens_output, "
        "total_cost, completed_at FROM run_metrics WHERE workflow_run_id = ?",
        (workflow_id,),
    ).fetchone()
    conn.close()
    return row


def _make_provider(result: dict, usage: LLMUsage | None = None) -> MagicMock:
    """Mock LLMClient that returns (result, typed usage) from complete_with_usage."""
    mock = MagicMock(spec=LLMClient)
    mock.provider_name = "claude"
    mock.model_name = "claude-haiku-4-5-20251001"
    mock.complete.return_value = result
    mock.complete_with_usage.return_value = (
        result, usage or LLMUsage(tokens_input=1000, tokens_output=200, cost_usd=0.0005),
    )
    return mock


def _score_result() -> dict:
    return {
        "job_id": "j1", "resume_id": "r1",
        "overall_score": 80, "technical_score": 80, "architecture_score": 70,
        "leadership_score": 60, "domain_score": 70,
        "match_summary": "ok", "strengths": [], "gaps": [],
        "recommended_next_action": "apply", "confidence": 80,
    }


# ── INVARIANT 1: every successful agent run produces exactly one llm_calls row
# This is the bug class that hid in production for weeks.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("agent_cls,context,result", [
    (ScoringAgent,
     {"job_id": "j1", "resume_id": "r1", "job_title": "Staff Engineer",
      "company": "Acme", "job_description": "Python at scale.",
      "resume_profile": {"name": "Jane", "skills": ["Python"]},
      "career_track": "all", "research_context": None},
     _score_result()),
])
def test_invariant_every_agent_run_writes_one_llm_call_row(
    db_path, obs_service, agent_cls, context, result,
):
    """If this fails, cost cannot be attributed per agent. That's the bug we
    shipped a fix for; this test prevents it coming back."""
    agent = agent_cls(_make_provider(result), obs_service)
    agent.run("wf-inv-1", context)
    assert _llm_call_count(db_path, "wf-inv-1") == 1


def test_invariant_n_agent_runs_produce_n_llm_call_rows(db_path, obs_service):
    """Concurrent / sequential calls must NOT aggregate inside BaseAgent.
    Aggregation in-memory is exactly how the previous bug stayed invisible."""
    agent = ScoringAgent(_make_provider(_score_result()), obs_service)
    ctx = {"job_id": "j1", "resume_id": "r1", "job_title": "Staff",
           "company": "Acme", "job_description": "Python.",
           "resume_profile": {"name": "Jane", "skills": ["Python"]},
           "career_track": "all", "research_context": None}
    for _ in range(5):
        agent.run("wf-inv-2", ctx)
    assert _llm_call_count(db_path, "wf-inv-2") == 5


# ── INVARIANT 2: llm_calls captures provider + model + cost — the columns
# needed for reconciliation against the provider's billing console
# ─────────────────────────────────────────────────────────────────────────────

def test_invariant_llm_calls_captures_provider_model_and_cost(db_path, obs_service):
    """The provider console reports billing per (provider, model). If our row
    doesn't capture both, reconciliation is impossible."""
    provider = _make_provider(
        _score_result(),
        LLMUsage(tokens_input=2500, tokens_output=400, cost_usd=0.00125),
    )
    provider.provider_name = "claude"
    provider.model_name = "claude-sonnet-4-6"

    agent = ScoringAgent(provider, obs_service)
    agent.run("wf-inv-3", {"job_id": "j1", "resume_id": "r1",
                            "job_title": "x", "company": "y",
                            "job_description": "z",
                            "resume_profile": {"name": "Jane", "skills": []},
                            "career_track": "all", "research_context": None})

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT provider, model, tokens_input, tokens_output, estimated_cost "
        "FROM llm_calls WHERE workflow_run_id = ?",
        ("wf-inv-3",),
    ).fetchone()
    conn.close()
    assert row[0] == "claude"
    assert row[1] == "claude-sonnet-4-6"  # not "unknown"
    assert row[2] == 2500
    assert row[3] == 400
    assert abs(row[4] - 0.00125) < 1e-9


# ── INVARIANT 3: every workflow has a run_metrics row from start to finish
# ─────────────────────────────────────────────────────────────────────────────

def test_invariant_register_run_creates_run_metrics_row(db_path, obs_service):
    """Without this row, finalize_run_metrics in generate_report is a no-op
    UPDATE on zero rows. Cost rollup silently does not persist."""
    workflow_repo = WorkflowRepository(db_path=db_path)
    node = make_register_run_node(workflow_repo, observability=obs_service)
    node({"workflow_id": "wf-inv-4", "workflow_type": "full_career_review"})

    row = _run_metrics_row(db_path, "wf-inv-4")
    assert row is not None
    # Initial state: zeros, completed_at NULL.
    assert row[0] == 0
    assert row[4] is None


def test_invariant_generate_report_finalizes_run_metrics_from_llm_calls(
    db_path, obs_service,
):
    """The whole point of the run_metrics row is the END-OF-RUN totals. If
    finalize doesn't read from llm_calls (the truth source), the rollup
    drifts from what the provider actually billed."""
    workflow_repo = WorkflowRepository(db_path=db_path)
    obs_repo = ObservabilityRepository(db_path=db_path)

    workflow_repo.create("wf-inv-5", "full_career_review",
                          {"workflow_id": "wf-inv-5"})
    obs_service.init_run_metrics("wf-inv-5", utcnow_iso())

    # Seed two llm_calls rows.
    obs_repo.create_llm_call("c1", "wf-inv-5", "scoring_agent", "claude",
                              "claude-haiku-4-5-20251001",
                              tokens_input=1000, tokens_output=200,
                              estimated_cost=0.0005, latency_ms=120)
    obs_repo.create_llm_call("c2", "wf-inv-5", "tailoring_agent", "claude",
                              "claude-sonnet-4-6",
                              tokens_input=3000, tokens_output=600,
                              estimated_cost=0.018, latency_ms=4200)

    report_gen = MagicMock()
    report_gen.generate_run_summary.return_value = "# Report"
    node = make_generate_report_node(report_gen, obs_service, workflow_repo)
    node({"workflow_id": "wf-inv-5", "errors": []})

    row = _run_metrics_row(db_path, "wf-inv-5")
    assert row is not None
    assert row[0] == 2                         # total_llm_calls
    assert row[1] == 4000                      # total_tokens_input
    assert row[2] == 800                       # total_tokens_output
    assert abs(row[3] - 0.0185) < 1e-9         # total_cost
    assert row[4] is not None                  # completed_at populated


# ── INVARIANT 4: agent_events count matches llm_calls count for successful runs
# (one llm call per completed agent_event with status="completed")
# ─────────────────────────────────────────────────────────────────────────────

def test_invariant_agent_events_completed_equals_llm_calls_count(
    db_path, obs_service,
):
    """If these diverge, an agent ran but didn't bill — or billed but didn't
    log a completion. Either way the audit trail is broken."""
    agent = ScoringAgent(_make_provider(_score_result()), obs_service)
    ctx = {"job_id": "j1", "resume_id": "r1", "job_title": "x",
           "company": "y", "job_description": "z",
           "resume_profile": {"name": "Jane", "skills": []},
           "career_track": "all", "research_context": None}
    for _ in range(4):
        agent.run("wf-inv-6", ctx)

    conn = sqlite3.connect(str(db_path))
    completed = conn.execute(
        "SELECT COUNT(*) FROM agent_events "
        "WHERE workflow_run_id=? AND event_type='completed'",
        ("wf-inv-6",),
    ).fetchone()[0]
    llm_calls = conn.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE workflow_run_id=?",
        ("wf-inv-6",),
    ).fetchone()[0]
    conn.close()
    assert completed == llm_calls


# ── INVARIANT 5: failed agent runs leave a "failed" event but NO llm_call row
# ─────────────────────────────────────────────────────────────────────────────

def test_invariant_failed_runs_do_not_write_llm_call_row(db_path, obs_service):
    """A failure before the provider responded means the provider didn't bill,
    so there must be no llm_calls row. A failure AFTER provider response
    (e.g. schema repair failure) IS billed and SHOULD have a row — that case
    is harder to test deterministically; this test covers the cleaner half."""
    from app.providers.llm_client import LLMProviderError

    bad_provider = MagicMock(spec=LLMClient)
    bad_provider.provider_name = "claude"
    bad_provider.model_name = "claude-haiku-4-5-20251001"
    bad_provider.complete_with_usage.side_effect = LLMProviderError("api timeout")
    bad_provider.complete.side_effect = LLMProviderError("api timeout")

    agent = ScoringAgent(bad_provider, obs_service)
    ctx = {"job_id": "j1", "resume_id": "r1", "job_title": "x",
           "company": "y", "job_description": "z",
           "resume_profile": {"name": "Jane", "skills": []},
           "career_track": "all", "research_context": None}
    with pytest.raises(LLMProviderError):
        agent.run("wf-inv-7", ctx)

    assert _llm_call_count(db_path, "wf-inv-7") == 0
    # But the failed event MUST exist for traceability.
    conn = sqlite3.connect(str(db_path))
    failed_events = conn.execute(
        "SELECT COUNT(*) FROM agent_events "
        "WHERE workflow_run_id=? AND event_type='failed'",
        ("wf-inv-7",),
    ).fetchone()[0]
    conn.close()
    assert failed_events == 1
