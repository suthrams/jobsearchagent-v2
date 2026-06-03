"""Cost-logging completeness invariant (ADR-077).

The "every billed LLM call writes an llm_calls row" rule was enforced only by
convention across three copies of the pattern (BaseAgent, the resume-parser fn,
CustomUrlScraper). The original cost-undercount bug shipped because nothing
guarded it. These tests guard the failure-path half specifically:

  1. Behavioral - a failed call that carries usage on the exception logs exactly
     one llm_calls row (and still re-raises); a failed call with no usage logs none.
  2. Forcing-function - BaseAgent's failure path must contain a log_llm_call, and
     the known LLM-call sites must each log cost (so the seam cannot silently rot).

Mirrors feedback_test_invariants_for_critical_concerns: a load-bearing system
promise needs an invariant test that spans the seam, not just module-mock units.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.base_agent import BaseAgent
from app.providers.llm_client import LLMProviderError, LLMUsage

APP_DIR = Path(__file__).resolve().parents[2] / "app"


class _ProbeAgent(BaseAgent):
    """Minimal concrete agent for exercising BaseAgent._run."""
    AGENT_NAME = "probe_agent"

    def run(self, workflow_id: str, context: dict):  # pragma: no cover - unused
        return self._run(workflow_id, context, dict)


def _provider_that_raises(exc: Exception) -> MagicMock:
    p = MagicMock()
    p.provider_name = "claude"
    p.model_name = "claude-test"
    p.complete_with_usage.side_effect = exc
    return p


# ── Layer 1: behavioral ───────────────────────────────────────────────────────


def test_failed_call_with_usage_logs_one_cost_row():
    """ADR-077: a billed-but-unparseable failure (usage attached to the error)
    writes exactly one llm_calls row, logs the agent failure, and re-raises."""
    obs = MagicMock()
    usage = LLMUsage(tokens_input=200, tokens_output=40, cost_usd=0.01)
    provider = _provider_that_raises(LLMProviderError("repair failed", usage=usage))
    agent = _ProbeAgent(provider, obs)

    with pytest.raises(LLMProviderError):
        agent._run("wf-1", {"job_id": "j1"}, dict)

    obs.log_agent_failed.assert_called_once()
    obs.log_llm_call.assert_called_once()
    kwargs = obs.log_llm_call.call_args.kwargs
    assert kwargs["workflow_id"] == "wf-1"
    assert kwargs["agent_name"] == "probe_agent"
    assert kwargs["tokens_input"] == 200
    assert kwargs["tokens_output"] == 40
    assert kwargs["cost_usd"] == 0.01


def test_failed_call_without_usage_logs_no_cost_row():
    """A transient failure (no usage on the error) logs the agent failure but NO
    llm_calls row - nothing was billed, so nothing should be attributed."""
    obs = MagicMock()
    provider = _provider_that_raises(LLMProviderError("connection reset"))  # usage=None
    agent = _ProbeAgent(provider, obs)

    with pytest.raises(LLMProviderError):
        agent._run("wf-1", {"job_id": "j1"}, dict)

    obs.log_agent_failed.assert_called_once()
    obs.log_llm_call.assert_not_called()


def test_zero_usage_failure_logs_no_cost_row():
    """A failure carrying an all-zero usage is treated as un-billed (no row)."""
    obs = MagicMock()
    provider = _provider_that_raises(LLMProviderError("empty", usage=LLMUsage()))
    agent = _ProbeAgent(provider, obs)
    with pytest.raises(LLMProviderError):
        agent._run("wf-1", {"job_id": "j1"}, dict)
    obs.log_llm_call.assert_not_called()


# ── Layer 2: forcing function ─────────────────────────────────────────────────


def test_base_agent_failure_path_logs_cost():
    """BaseAgent must log_llm_call on BOTH the success and the failure path.

    Two occurrences in base_agent.py: the success-path row and the ADR-077
    failure-path row. If a refactor drops the failure-path logging, failed-call
    spend silently vanishes again - this fails the build first.
    """
    src = (APP_DIR / "agents" / "base_agent.py").read_text(encoding="utf-8")
    assert src.count("log_llm_call(") >= 2, (
        "base_agent.py must log_llm_call on both success and failure paths (ADR-077)"
    )


def test_llm_call_sites_log_cost():
    """Every module that makes an LLM call must also log it to llm_calls.

    Guards the cost-attribution completeness invariant across the three copies of
    the pattern (BaseAgent, the resume-parser fn, CustomUrlScraper). A fourth call
    site that forgets log_llm_call fails this.
    """
    sites = 0
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "log_llm_call(" in text and "def log_llm_call" not in text:
            sites += 1
    assert sites >= 3, (
        f"expected log_llm_call at the known LLM-call sites, found {sites}. "
        "A billed call may be going unlogged (ADR-077 completeness invariant)."
    )
