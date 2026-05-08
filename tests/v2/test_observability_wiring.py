"""Tests for the observability wiring fix.

Before this fix the llm_calls and run_metrics tables were empty in production
because BaseAgent._run never called log_llm_call and register_run never called
init_run_metrics. This file locks in the new behavior so the cost-attribution
audit trail can't regress silently.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.scoring_agent import ScoringAgent
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
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


@pytest.fixture
def obs_service(db_path) -> ObservabilityService:
    obs_repo = ObservabilityRepository(db_path=db_path)
    step_repo = StepRepository(db_path=db_path)
    dec_repo = DecisionRepository(db_path=db_path)
    sec_repo = SecurityRepository(db_path=db_path)
    return ObservabilityService(obs_repo, step_repo, dec_repo, sec_repo)


def _score_result() -> dict:
    return {
        "job_id": "job-001", "resume_id": "res-001",
        "overall_score": 82, "technical_score": 88,
        "architecture_score": 75, "leadership_score": 60, "domain_score": 70,
        "match_summary": "Strong technical fit.",
        "strengths": ["Python"], "gaps": ["Limited leadership"],
        "recommended_next_action": "Apply", "confidence": 85,
    }


def _make_provider(usage: LLMUsage | None = None) -> MagicMock:
    """A mock LLMClient that returns a result + typed usage from complete_with_usage."""
    mock = MagicMock(spec=LLMClient)
    mock.provider_name = "claude"
    mock.model_name = "claude-haiku-4-5-20251001"
    mock.complete.return_value = _score_result()
    mock.complete_with_usage.return_value = (
        _score_result(),
        usage or LLMUsage(tokens_input=1500, tokens_output=300, cost_usd=0.0008),
    )
    return mock


_CONTEXT = {
    "job_id": "job-001", "resume_id": "res-001",
    "job_title": "Staff Engineer", "company": "Acme",
    "job_description": "Python at scale.",
    "resume_profile": {"name": "Jane", "skills": ["Python"]},
    "career_track": "all",
    "research_context": None,
}


# ── log_llm_call wiring ──────────────────────────────────────────────────────

def test_base_agent_writes_one_llm_call_row_per_run(db_path, obs_service):
    """The bug: BaseAgent._run never called log_llm_call, so llm_calls stayed
    empty. Asserting that one ScoringAgent.run() now writes exactly one row."""
    agent = ScoringAgent(_make_provider(), obs_service)
    agent.run("wf-test-1", _CONTEXT)

    repo = ObservabilityRepository(db_path=db_path)
    rows = repo.get_llm_calls_by_run("wf-test-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["agent_name"] == "scoring_agent"
    assert row["provider"] == "claude"
    assert row["model"] == "claude-haiku-4-5-20251001"
    assert row["tokens_input"] == 1500
    assert row["tokens_output"] == 300
    assert abs(row["estimated_cost"] - 0.0008) < 1e-9
    # Latency comes from time.monotonic() bracketing the provider call; should
    # always be >= 0 and finite.
    assert row["latency_ms"] >= 0


def test_three_runs_produce_three_llm_call_rows(db_path, obs_service):
    """Each agent call gets its own row; nothing aggregates inside BaseAgent."""
    agent = ScoringAgent(_make_provider(), obs_service)
    agent.run("wf-test-2", _CONTEXT)
    agent.run("wf-test-2", _CONTEXT)
    agent.run("wf-test-2", _CONTEXT)

    repo = ObservabilityRepository(db_path=db_path)
    assert len(repo.get_llm_calls_by_run("wf-test-2")) == 3


def test_base_agent_persists_cache_token_breakdown(db_path, obs_service):
    """BaseAgent must forward the prompt-cache split to log_llm_call so the Cost
    Dashboard can compute the cache-hit ratio. Without this the cache columns
    on llm_calls stay zero even when caching is active."""
    usage = LLMUsage(
        tokens_input=2000,
        tokens_output=300,
        cost_usd=0.0012,
        cache_creation_tokens=400,
        cache_read_tokens=1200,
    )
    agent = ScoringAgent(_make_provider(usage=usage), obs_service)
    agent.run("wf-cache-1", _CONTEXT)

    repo = ObservabilityRepository(db_path=db_path)
    rows = repo.get_llm_calls_by_run("wf-cache-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["cache_creation_tokens"] == 400
    assert row["cache_read_tokens"] == 1200


def test_log_llm_call_failure_does_not_crash_agent(db_path):
    """Observability must never crash the run. If the obs repo raises, the
    agent still returns its result."""
    failing_obs = MagicMock(spec=ObservabilityService)
    failing_obs.log_agent_started.return_value = "evt-1"
    failing_obs.log_llm_call.side_effect = RuntimeError("disk full")
    agent = ScoringAgent(_make_provider(), failing_obs)
    # Should NOT raise — log_llm_call failure is swallowed.
    result = agent.run("wf-test-3", _CONTEXT)
    assert result.overall_score == 82


# ── run_metrics lifecycle wiring ─────────────────────────────────────────────

def test_register_run_node_creates_run_metrics_row(db_path, obs_service):
    """register_run was missing the create_run_metrics call; finalize was a
    no-op as a result. Asserting the row now exists after register_run runs."""
    workflow_repo = WorkflowRepository(db_path=db_path)
    node = make_register_run_node(workflow_repo, observability=obs_service)

    state = {
        "workflow_id": "wf-init-1",
        "workflow_type": "full_career_review",
    }
    node(state)

    # run_metrics row should exist with zeros (will be updated at end of run)
    obs_repo = ObservabilityRepository(db_path=db_path)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT total_llm_calls, total_cost FROM run_metrics WHERE workflow_run_id=?",
        ("wf-init-1",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0
    assert row[1] == 0.0


def test_finalize_run_metrics_uses_llm_calls_as_truth_source(db_path, obs_service):
    """The state_json aggregator is lossy. The finalize step rolls up totals
    from llm_calls (canonical) and writes them to run_metrics."""
    obs_repo = ObservabilityRepository(db_path=db_path)
    workflow_repo = WorkflowRepository(db_path=db_path)

    # Initialise the run row + run_metrics row.
    workflow_repo.create("wf-final-1", "full_career_review", {"workflow_id": "wf-final-1"})
    obs_service.init_run_metrics("wf-final-1", utcnow_iso())

    # Simulate three llm_calls landing during the run.
    for i, (ti, to, cost) in enumerate([
        (1500, 300, 0.0008),
        (5000, 1200, 0.025),
        (800, 200, 0.0005),
    ]):
        obs_repo.create_llm_call(
            call_id=f"call-{i}",
            workflow_run_id="wf-final-1",
            agent_name="scoring_agent",
            provider="claude",
            model="claude-haiku-4-5-20251001",
            tokens_input=ti, tokens_output=to,
            estimated_cost=cost, latency_ms=100,
        )

    # Run the generate_report node — it should finalize the run_metrics row.
    report_gen = MagicMock()
    report_gen.generate_run_summary.return_value = "# Report"
    node = make_generate_report_node(report_gen, obs_service, workflow_repo)
    state = {"workflow_id": "wf-final-1", "errors": []}
    node(state)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT total_llm_calls, total_tokens_input, total_tokens_output, total_cost "
        "FROM run_metrics WHERE workflow_run_id=?",
        ("wf-final-1",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 3
    assert row[1] == 1500 + 5000 + 800
    assert row[2] == 300 + 1200 + 200
    assert abs(row[3] - (0.0008 + 0.025 + 0.0005)) < 1e-9
