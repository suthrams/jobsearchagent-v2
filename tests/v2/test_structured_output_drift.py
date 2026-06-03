"""Structured-output repair rate as a behavioral-drift proxy (ADR-078).

A schema-repair pass is the cheapest available drift signal: a rising per-agent
repair rate flags output-shape drift or a provider-side change. These tests cover
the two halves the provider tests don't:

  1. BaseAgent emits a `schema_repaired` agent_event when a call needed a repair,
     on both the success and the repair-exhausted-failure path, and emits nothing
     on a clean call.
  2. system_health.reliability_summary counts `schema_repaired` events,
     profile-scoped, without those rows polluting the failure or latency rollups.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.base_agent import BaseAgent
from app.providers.llm_client import LLMProviderError, LLMUsage
from app.repositories.database import init_db
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services import system_health as sh


class _ProbeAgent(BaseAgent):
    AGENT_NAME = "probe_agent"

    def run(self, workflow_id: str, context: dict):  # pragma: no cover - unused
        return self._run(workflow_id, context, dict)


def _provider(*, returns=None, raises=None) -> MagicMock:
    p = MagicMock()
    p.provider_name = "claude"
    p.model_name = "claude-test"
    if raises is not None:
        p.complete_with_usage.side_effect = raises
    else:
        p.complete_with_usage.return_value = returns
    return p


# ── Layer 1: BaseAgent emission ───────────────────────────────────────────────


def test_emits_schema_repaired_on_success_when_repaired():
    obs = MagicMock()
    provider = _provider(returns=({"ok": True}, LLMUsage(tokens_input=10, schema_repairs=1)))
    _ProbeAgent(provider, obs)._run("wf-1", {"job_id": "j1"}, dict)
    obs.log_schema_repair.assert_called_once_with("wf-1", "probe_agent")


def test_no_schema_repaired_event_on_clean_call():
    obs = MagicMock()
    provider = _provider(returns=({"ok": True}, LLMUsage(tokens_input=10, schema_repairs=0)))
    _ProbeAgent(provider, obs)._run("wf-1", {"job_id": "j1"}, dict)
    obs.log_schema_repair.assert_not_called()


def test_emits_schema_repaired_on_repair_exhausted_failure():
    obs = MagicMock()
    err = LLMProviderError("repair failed", usage=LLMUsage(tokens_input=10, schema_repairs=1))
    provider = _provider(raises=err)
    with pytest.raises(LLMProviderError):
        _ProbeAgent(provider, obs)._run("wf-1", {"job_id": "j1"}, dict)
    obs.log_schema_repair.assert_called_once_with("wf-1", "probe_agent")


# ── Layer 2: rollup + non-pollution ───────────────────────────────────────────


def test_reliability_summary_counts_schema_repairs(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    wf_repo = WorkflowRepository(db)
    obs_repo = ObservabilityRepository(db)
    wf_repo.create("run-1", "job_search", {"user_id": "1", "status": "completed"})

    # two repairs + one ordinary failure on the same run/profile
    obs_repo.create_agent_event("e1", "run-1", "scoring_agent", "schema_repaired", "repaired")
    obs_repo.create_agent_event("e2", "run-1", "research_agent", "schema_repaired", "repaired")
    obs_repo.create_agent_event("e3", "run-1", "scoring_agent", "failed", "failed",
                                duration_ms=12, output_summary="boom")

    summ = sh.reliability_summary(user_id="1", db_path=db)
    assert summ["schema_repairs"] == 2
    # the repaired rows must NOT be counted as failures (status='repaired')
    assert summ["agent_failures"] == 1
    # another profile sees none of run-1's repairs
    assert sh.reliability_summary(user_id="2", db_path=db)["schema_repairs"] == 0
