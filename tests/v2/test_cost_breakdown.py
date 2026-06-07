"""Tests for cost_breakdown service — ADR-053."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from app.services.cost_breakdown import (
    all_runs_by_cost,
    compute_breakdown,
    compute_dashboard_aggregate,
    daily_spend_trend,
    weekly_spend_trend,
    to_markdown,
    top_calls_by_cost,
    top_runs_by_cost,
)
from app.repositories.database import init_db


def _seed_calls(db_path: Path, workflow_id: str, calls: list[dict]) -> None:
    """Insert llm_call rows for a workflow.

    Each call dict may include `cache_w` and `cache_r` for the prompt-cache
    breakdown; both default to 0 when omitted so existing fixtures still work.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        for c in calls:
            conn.execute(
                """INSERT INTO llm_calls
                   (id, workflow_run_id, agent_name, provider, model,
                    tokens_input, tokens_output, cache_creation_tokens,
                    cache_read_tokens, estimated_cost, latency_ms,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), workflow_id, c["agent"],
                 _provider_for(c["model"]), c["model"],
                 c["t_in"], c["t_out"],
                 int(c.get("cache_w", 0)), int(c.get("cache_r", 0)),
                 c["cost"], c["latency"],
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


# ── Cross-run aggregations (Cost Dashboard view) ─────────────────────────────

def _seed_calls_at(db_path: Path, workflow_id: str, when_iso: str, calls: list[dict]) -> None:
    """Insert llm_call rows with an explicit created_at timestamp."""
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
                 c["t_in"], c["t_out"], c["cost"], c["latency"], when_iso),
            )
        conn.commit()
    finally:
        conn.close()


def test_dashboard_aggregate_totals_and_per_agent_per_model(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    _seed_calls(db, "wf-1", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 1000, "t_out": 200, "cost": 0.001, "latency": 100},
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 1000, "t_out": 200, "cost": 0.001, "latency": 100},
        {"agent": "tailoring_agent", "model": "claude-sonnet-4-6",
         "t_in": 3000, "t_out": 600, "cost": 0.018, "latency": 4200},
    ])
    _seed_calls(db, "wf-2", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 500, "t_out": 100, "cost": 0.0005, "latency": 80},
    ])

    out = compute_dashboard_aggregate(days=None, db_path=db)
    assert out["totals"]["calls"] == 4
    assert out["totals"]["tokens_input"] == 5500
    assert out["totals"]["tokens_output"] == 1100
    assert out["totals"]["cost_usd"] == pytest.approx(0.0205)
    assert out["totals"]["distinct_runs"] == 2

    agents = {r["agent_name"]: r for r in out["by_agent"]}
    assert agents["tailoring_agent"]["cost_usd"] == pytest.approx(0.018)
    assert agents["scoring_agent"]["calls"] == 3

    models = {r["model"]: r for r in out["by_model"]}
    assert models["claude-haiku-4-5-20251001"]["calls"] == 3
    assert models["claude-sonnet-4-6"]["cost_usd"] == pytest.approx(0.018)
    assert models["claude-sonnet-4-6"]["provider"] == "claude"


def test_dashboard_window_filters_by_age(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    # One row right now (in window) + one row 60 days ago (outside any 7/30 window).
    _seed_calls_at(db, "wf-recent", "2099-01-01T00:00:00Z", [  # tomorrow-proof: future date
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.0001, "latency": 100},
    ])
    _seed_calls_at(db, "wf-old", "2020-01-01T00:00:00Z", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.99, "latency": 100},
    ])
    # All-time sees both
    all_time = compute_dashboard_aggregate(days=None, db_path=db)
    assert all_time["totals"]["calls"] == 2
    # 7-day window excludes the old row (and excludes the future row too — that's
    # the safer behavior; we verify the OLD row is excluded specifically)
    last_7 = compute_dashboard_aggregate(days=7, db_path=db)
    # Old row's $0.99 must NOT be in the windowed total
    assert last_7["totals"]["cost_usd"] < 0.99


def test_dashboard_empty_when_no_calls(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    out = compute_dashboard_aggregate(days=7, db_path=db)
    assert out["totals"]["calls"] == 0
    assert out["by_agent"] == []
    assert out["by_model"] == []
    # Cache fields are present even on the empty path so the UI can render.
    assert out["totals"]["cache_creation_tokens"] == 0
    assert out["totals"]["cache_read_tokens"] == 0
    assert out["totals"]["cache_hit_ratio"] == 0.0


def test_breakdown_surfaces_cache_hit_ratio_per_agent(tmp_path):
    """Per-agent rows must expose cache_creation_tokens, cache_read_tokens, and
    a hit ratio so the Cost Dashboard can show whether prompt caching is
    actually landing for each agent."""
    db = tmp_path / "v2.db"
    init_db(db)
    wf = "wf-cache-001"
    _seed_calls(db, wf, [
        # research_agent: 80% reads, 20% writes — caching is working
        {"agent": "research_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 1000, "t_out": 50, "cost": 0.001, "latency": 1000,
         "cache_w": 200, "cache_r": 800},
        # career_advisor: no cache activity at all — caching not landing
        {"agent": "career_advisor", "model": "claude-sonnet-4-6",
         "t_in": 1000, "t_out": 200, "cost": 0.005, "latency": 4000,
         "cache_w": 0, "cache_r": 0},
    ])
    out = compute_breakdown(workflow_id=wf, db_path=db)
    rows = {r["agent_name"]: r for r in out["rows"]}

    assert rows["research_agent"]["cache_creation_tokens"] == 200
    assert rows["research_agent"]["cache_read_tokens"] == 800
    # 800 reads / 1000 total billable input = 80%
    assert rows["research_agent"]["cache_hit_ratio"] == pytest.approx(0.8)

    assert rows["career_advisor"]["cache_hit_ratio"] == 0.0

    # Aggregate sums + computes ratio across all calls
    agg = out["aggregate"]
    assert agg["cache_creation_tokens"] == 200
    assert agg["cache_read_tokens"] == 800
    # 800 / (1000 + 1000) = 0.4
    assert agg["cache_hit_ratio"] == pytest.approx(0.4)


def test_dashboard_aggregates_cache_tokens_across_runs(tmp_path):
    """Cross-run aggregation must sum cache tokens and report a system-wide hit
    ratio. The dashboard's 'cache effectiveness' panel reads these."""
    db = tmp_path / "v2.db"
    init_db(db)
    _seed_calls(db, "wf-1", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 2000, "t_out": 100, "cost": 0.002, "latency": 1000,
         "cache_w": 500, "cache_r": 1500},
    ])
    _seed_calls(db, "wf-2", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 1000, "t_out": 50, "cost": 0.001, "latency": 800,
         "cache_w": 0, "cache_r": 1000},
    ])
    out = compute_dashboard_aggregate(days=None, db_path=db)
    totals = out["totals"]
    assert totals["cache_creation_tokens"] == 500
    assert totals["cache_read_tokens"] == 2500
    # 2500 reads / 3000 total billable input = 0.833...
    assert totals["cache_hit_ratio"] == pytest.approx(2500 / 3000)


def test_breakdown_cache_hit_ratio_is_zero_when_no_input(tmp_path):
    """Defensive: hit ratio must not divide by zero when input tokens are 0."""
    db = tmp_path / "v2.db"
    init_db(db)
    out = compute_breakdown(workflow_id="missing-run", db_path=db)
    assert out["aggregate"]["cache_hit_ratio"] == 0.0


def test_top_runs_by_cost_orders_descending(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    _seed_calls(db, "wf-cheap", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.001, "latency": 100},
    ])
    _seed_calls(db, "wf-expensive", [
        {"agent": "tailoring_agent", "model": "claude-sonnet-4-6",
         "t_in": 5000, "t_out": 1000, "cost": 0.030, "latency": 4000},
    ])
    _seed_calls(db, "wf-medium", [
        {"agent": "career_advisor", "model": "claude-sonnet-4-6",
         "t_in": 2000, "t_out": 400, "cost": 0.012, "latency": 2000},
    ])
    runs = top_runs_by_cost(n=3, days=None, db_path=db)
    assert [r["workflow_run_id"] for r in runs] == ["wf-expensive", "wf-medium", "wf-cheap"]
    assert runs[0]["cost_usd"] == pytest.approx(0.030)


def test_top_calls_by_cost_returns_individual_calls(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    _seed_calls(db, "wf-1", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.001, "latency": 100},
        {"agent": "tailoring_agent", "model": "claude-sonnet-4-6",
         "t_in": 5000, "t_out": 1000, "cost": 0.030, "latency": 4000},
        {"agent": "career_advisor", "model": "claude-sonnet-4-6",
         "t_in": 2000, "t_out": 400, "cost": 0.012, "latency": 2000},
    ])
    top = top_calls_by_cost(n=2, days=None, db_path=db)
    assert len(top) == 2
    assert top[0]["cost_usd"] == pytest.approx(0.030)
    assert top[0]["agent_name"] == "tailoring_agent"
    assert top[1]["cost_usd"] == pytest.approx(0.012)


def test_daily_spend_trend_groups_by_day(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    # Two calls on same day -> one trend row with summed cost.
    _seed_calls_at(db, "wf-1", "2099-01-01T10:00:00Z", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.001, "latency": 100},
    ])
    _seed_calls_at(db, "wf-1", "2099-01-01T15:00:00Z", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.002, "latency": 100},
    ])
    # Window is 30 days; we use a future date so it's always in-window during test.
    trend = daily_spend_trend(days=365 * 100, db_path=db)
    days = {r["day"]: r for r in trend}
    assert "2099-01-01" in days
    assert days["2099-01-01"]["calls"] == 2
    assert days["2099-01-01"]["cost_usd"] == pytest.approx(0.003)


def test_weekly_spend_trend_groups_by_week(tmp_path):
    db = tmp_path / "v2.db"
    init_db(db)
    # Two calls in the same ISO week -> one weekly row with summed cost.
    _seed_calls_at(db, "wf-1", "2099-06-15T10:00:00Z", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.001, "latency": 100},
    ])
    _seed_calls_at(db, "wf-1", "2099-06-15T15:00:00Z", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.002, "latency": 100},
    ])
    trend = weekly_spend_trend(days=365 * 100, db_path=db)
    assert len(trend) == 1
    assert "week" in trend[0]
    assert trend[0]["calls"] == 2
    assert trend[0]["cost_usd"] == pytest.approx(0.003)
    assert weekly_spend_trend(days=7, db_path=tmp_path / "missing.db") == []


def test_dashboard_returns_zeros_when_db_missing(tmp_path):
    out = compute_dashboard_aggregate(days=7, db_path=tmp_path / "missing.db")
    assert out["totals"]["calls"] == 0
    assert top_runs_by_cost(n=5, days=None, db_path=tmp_path / "missing.db") == []
    assert top_calls_by_cost(n=10, days=None, db_path=tmp_path / "missing.db") == []
    assert daily_spend_trend(days=7, db_path=tmp_path / "missing.db") == []
    assert all_runs_by_cost(days=None, db_path=tmp_path / "missing.db") == []


def test_all_runs_by_cost_includes_every_run_unlike_top_n(tmp_path):
    """all_runs_by_cost has no LIMIT — it must return every distinct workflow_run_id
    in the window, ordered by cost desc. The 'all runs by cost' table in the
    dashboard depends on this."""
    db = tmp_path / "v2.db"
    init_db(db)
    for i in range(7):
        _seed_calls(db, f"wf-{i}", [
            {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
             "t_in": 100, "t_out": 50, "cost": (i + 1) * 0.001, "latency": 100},
        ])
    runs = all_runs_by_cost(days=None, db_path=db)
    assert len(runs) == 7
    # Highest cost first.
    assert runs[0]["workflow_run_id"] == "wf-6"
    assert runs[-1]["workflow_run_id"] == "wf-0"
    # And it includes runs that top_runs_by_cost(n=5) would exclude.
    top5 = top_runs_by_cost(n=5, days=None, db_path=db)
    top5_ids = {r["workflow_run_id"] for r in top5}
    assert "wf-0" not in top5_ids
    assert any(r["workflow_run_id"] == "wf-0" for r in runs)


def test_all_runs_by_cost_window_filter(tmp_path):
    """Same window-filtering semantics as the other dashboard helpers."""
    db = tmp_path / "v2.db"
    init_db(db)
    _seed_calls_at(db, "wf-old", "2020-01-01T00:00:00Z", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.99, "latency": 100},
    ])
    _seed_calls_at(db, "wf-new", "2099-01-01T00:00:00Z", [
        {"agent": "scoring_agent", "model": "claude-haiku-4-5-20251001",
         "t_in": 100, "t_out": 50, "cost": 0.001, "latency": 100},
    ])
    # 7-day window excludes wf-old.
    last_7 = all_runs_by_cost(days=7, db_path=db)
    assert all(r["workflow_run_id"] != "wf-old" for r in last_7)
    # All-time includes both.
    all_time = all_runs_by_cost(days=None, db_path=db)
    assert {r["workflow_run_id"] for r in all_time} == {"wf-old", "wf-new"}
