"""Tests for cost_breakdown service — ADR-053."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from app.services.cost_breakdown import compute_breakdown, to_markdown
from app.repositories.database import init_db


def _seed_calls(db_path: Path, workflow_id: str, calls: list[dict]) -> None:
    """Insert llm_call rows for a workflow."""
    conn = sqlite3.connect(str(db_path))
    try:
        for c in calls:
            conn.execute(
                """INSERT INTO llm_calls
                   (id, workflow_run_id, agent_name, provider, model,
                    tokens_input, tokens_output, estimated_cost, latency_ms,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), workflow_id, c["agent"],
                 _provider_for(c["model"]), c["model"],
                 c["t_in"], c["t_out"], c["cost"], c["latency"],
                 "2026-05-02T00:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()


def _provider_for(model: str) -> str:
    if model.startswith("claude"):
        return "claude"
    if model.startswith(("gpt", "o1")):
        return "openai"
    return "other"


def test_breakdown_groups_by_agent_and_model(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    wf = "wf-cb-001"
    _seed_calls(db, wf, [
        {"agent": "research_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.001, "latency": 1000},
        {"agent": "research_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 200, "t_out": 80, "cost": 0.002, "latency": 1200},
        {"agent": "career_advisor", "model": "claude-sonnet-4-6",
         "t_in": 500, "t_out": 200, "cost": 0.018, "latency": 3400},
    ])

    out = compute_breakdown(wf, db_path=db)
    rows = out["rows"]
    assert len(rows) == 2

    # Sorted by cost desc — career_advisor first
    assert rows[0]["agent_name"] == "career_advisor"
    assert rows[0]["calls"] == 1
    assert rows[0]["provider"] == "claude"
    assert rows[0]["cost_usd"] == pytest.approx(0.018)

    research = rows[1]
    assert research["agent_name"] == "research_agent"
    assert research["calls"] == 2
    assert research["tokens_input"] == 300
    assert research["tokens_output"] == 130
    assert research["cost_usd"] == pytest.approx(0.003)
    assert research["avg_latency_ms"] == pytest.approx(1100.0)


def test_breakdown_provider_inference():
    """compute_breakdown infers provider from the model name."""
    from app.services.cost_breakdown import _provider_for_model
    assert _provider_for_model("claude-sonnet-4-6") == "claude"
    assert _provider_for_model("gpt-4o-mini") == "openai"
    assert _provider_for_model("o1") == "openai"
    assert _provider_for_model("mystery-model") == "other"
    assert _provider_for_model(None) == "other"


def test_breakdown_aggregate(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    wf = "wf-cb-002"
    _seed_calls(db, wf, [
        {"agent": "a", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.01, "latency": 1000},
        {"agent": "b", "model": "gpt-4o",
         "t_in": 100, "t_out": 50, "cost": 0.02, "latency": 2000},
    ])
    out = compute_breakdown(wf, db_path=db)
    agg = out["aggregate"]
    assert agg["calls"] == 2
    assert agg["tokens_input"] == 200
    assert agg["tokens_output"] == 100
    assert agg["cost_usd"] == pytest.approx(0.03)


def test_breakdown_empty_when_no_calls(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    out = compute_breakdown("wf-empty", db_path=db)
    assert out["rows"] == []
    assert out["aggregate"]["calls"] == 0


def test_to_markdown_renders_table_and_aggregate(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    wf = "wf-md-001"
    _seed_calls(db, wf, [
        {"agent": "research_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.001, "latency": 1000},
    ])
    out = compute_breakdown(wf, db_path=db)
    md = to_markdown(out)
    assert "Cost Breakdown" in md
    assert "research_agent" in md
    assert "Aggregate" in md
    assert "$0.0010" in md


def test_to_markdown_handles_empty():
    md = to_markdown({"rows": [], "aggregate": {}})
    assert "_No LLM calls recorded" in md
