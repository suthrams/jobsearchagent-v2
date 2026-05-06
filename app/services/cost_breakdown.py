"""Cost breakdown service — per-agent / per-model rollup over the llm_calls table.

Per ADR-053: this is the feedback loop that makes the per-agent provider/model
picker actionable. The user sees "career_advisor on gpt-4o cost $0.04" next to
"resume_critic on claude-sonnet-4-6 cost $0.18" and can rebalance.

Pure function so it can be called from both the markdown ReportGenerator and
the Streamlit Workflow Detail UI without duplicating logic.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.repositories.database import DEFAULT_DB_PATH


def _provider_for_model(model: str | None) -> str:
    """Best-effort guess at the provider from a model name. Unknown → 'other'."""
    if not model:
        return "other"
    m = model.lower()
    if m.startswith("claude"):
        return "claude"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    return "other"


def compute_breakdown(
    workflow_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    """Return a per-agent rollup + aggregate for one workflow run.

    Shape:
      {
        "rows": [
          {"agent_name": "research_agent", "provider": "claude", "model": "claude-haiku-4-5-...",
           "calls": 8, "tokens_input": 4200, "tokens_output": 1100,
           "cost_usd": 0.0024, "avg_latency_ms": 1250.0},
          ...
        ],
        "aggregate": {"calls": 21, "tokens_input": 23600, "tokens_output": 7200,
                      "cost_usd": 0.0788, "avg_latency_ms": 2100.0},
      }

    Returns empty rows + zeroed aggregate if no calls have been logged for the run.
    """
    if not Path(db_path).exists():
        return {"rows": [], "aggregate": _zero_aggregate()}

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT agent_name, model,
                   COUNT(*)               AS calls,
                   SUM(tokens_input)      AS tokens_in,
                   SUM(tokens_output)     AS tokens_out,
                   SUM(estimated_cost)    AS cost,
                   AVG(latency_ms)        AS avg_latency
            FROM llm_calls
            WHERE workflow_run_id = ?
            GROUP BY agent_name, model
            ORDER BY cost DESC
            """,
            (workflow_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"rows": [], "aggregate": _zero_aggregate()}
    finally:
        conn.close()

    out_rows: list[dict] = []
    for agent_name, model, calls, t_in, t_out, cost, avg_latency in rows:
        out_rows.append({
            "agent_name": agent_name or "?",
            "provider": _provider_for_model(model),
            "model": model or "?",
            "calls": int(calls or 0),
            "tokens_input": int(t_in or 0),
            "tokens_output": int(t_out or 0),
            "cost_usd": float(cost or 0.0),
            "avg_latency_ms": float(avg_latency or 0.0),
        })

    agg = {
        "calls": sum(r["calls"] for r in out_rows),
        "tokens_input": sum(r["tokens_input"] for r in out_rows),
        "tokens_output": sum(r["tokens_output"] for r in out_rows),
        "cost_usd": sum(r["cost_usd"] for r in out_rows),
        "avg_latency_ms": (
            sum(r["avg_latency_ms"] * r["calls"] for r in out_rows)
            / sum(r["calls"] for r in out_rows)
        ) if out_rows else 0.0,
    }
    return {"rows": out_rows, "aggregate": agg}


def to_markdown(breakdown: dict) -> str:
    """Render a breakdown dict as a markdown table for the run report."""
    rows = breakdown.get("rows") or []
    agg = breakdown.get("aggregate") or _zero_aggregate()

    if not rows:
        return "## Cost Breakdown\n\n_No LLM calls recorded for this run._\n"

    lines = [
        "## Cost Breakdown",
        "",
        "| Agent | Provider | Model | Calls | Tokens in | Tokens out | Cost (USD) | Avg latency |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['agent_name']} | {r['provider']} | `{r['model']}` "
            f"| {r['calls']} | {r['tokens_input']:,} | {r['tokens_output']:,} "
            f"| ${r['cost_usd']:.4f} | {int(r['avg_latency_ms'])} ms |"
        )
    lines.append(
        f"| **Aggregate** |  |  | **{agg['calls']}** "
        f"| **{agg['tokens_input']:,}** | **{agg['tokens_output']:,}** "
        f"| **${agg['cost_usd']:.4f}** | {int(agg['avg_latency_ms'])} ms |"
    )
    lines.append("")
    return "\n".join(lines)


def _zero_aggregate() -> dict:
    return {
        "calls": 0, "tokens_input": 0, "tokens_output": 0,
        "cost_usd": 0.0, "avg_latency_ms": 0.0,
    }


# ── Cross-run aggregations (Cost Dashboard view) ─────────────────────────────
# These power the system-wide Cost Dashboard. They mirror the Step 5/8 SQL in
# docs/cost_troubleshooting.md so the UI surfaces the same numbers a developer
# would compute by hand. Pure functions over llm_calls, no side effects.
#
# All time-window arguments are in days. Pass days=None to scan all-time.


def _window_clause(days: int | None) -> str:
    """Return a SQL fragment scoped to the last N days, or empty for all-time."""
    if days is None or days <= 0:
        return ""
    return f"WHERE created_at >= datetime('now', '-{int(days)} days')"


def compute_dashboard_aggregate(
    days: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    """Total spend + per-agent + per-model rollups for the time window.

    Returns:
      {
        "window_days": int | None,
        "totals": {"calls": int, "tokens_input": int, "tokens_output": int,
                   "cost_usd": float, "distinct_runs": int},
        "by_agent": [{"agent_name": ..., "calls": ..., "cost_usd": ...}, ...],
        "by_model": [{"model": ..., "provider": ..., "calls": ..., "cost_usd": ...}, ...],
      }
    """
    if not Path(db_path).exists():
        return _empty_dashboard(days)
    conn = sqlite3.connect(str(db_path))
    where = _window_clause(days)
    try:
        totals_row = conn.execute(
            f"""
            SELECT COUNT(*)                       AS calls,
                   COALESCE(SUM(tokens_input), 0)   AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)  AS tokens_out,
                   COALESCE(SUM(estimated_cost), 0) AS cost,
                   COUNT(DISTINCT workflow_run_id)  AS distinct_runs
            FROM llm_calls
            {where}
            """,
        ).fetchone()
        agent_rows = conn.execute(
            f"""
            SELECT agent_name,
                   COUNT(*)                         AS calls,
                   COALESCE(SUM(tokens_input), 0)   AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)  AS tokens_out,
                   COALESCE(SUM(estimated_cost), 0) AS cost
            FROM llm_calls
            {where}
            GROUP BY agent_name
            ORDER BY cost DESC
            """,
        ).fetchall()
        model_rows = conn.execute(
            f"""
            SELECT model,
                   COUNT(*)                         AS calls,
                   COALESCE(SUM(tokens_input), 0)   AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)  AS tokens_out,
                   COALESCE(SUM(estimated_cost), 0) AS cost
            FROM llm_calls
            {where}
            GROUP BY model
            ORDER BY cost DESC
            """,
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return _empty_dashboard(days)
    finally:
        conn.close()

    return {
        "window_days": days,
        "totals": {
            "calls":         int(totals_row[0] or 0),
            "tokens_input":  int(totals_row[1] or 0),
            "tokens_output": int(totals_row[2] or 0),
            "cost_usd":      float(totals_row[3] or 0.0),
            "distinct_runs": int(totals_row[4] or 0),
        },
        "by_agent": [
            {"agent_name": a or "?", "calls": int(c or 0),
             "tokens_input": int(ti or 0), "tokens_output": int(to or 0),
             "cost_usd": float(cost or 0.0)}
            for a, c, ti, to, cost in agent_rows
        ],
        "by_model": [
            {"model": m or "?", "provider": _provider_for_model(m),
             "calls": int(c or 0),
             "tokens_input": int(ti or 0), "tokens_output": int(to or 0),
             "cost_usd": float(cost or 0.0)}
            for m, c, ti, to, cost in model_rows
        ],
    }


def daily_spend_trend(
    days: int = 30,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """One row per day for the last N days. Days with zero spend are omitted —
    the UI fills in zeros if needed."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            f"""
            SELECT DATE(created_at)              AS day,
                   COUNT(*)                      AS calls,
                   COALESCE(SUM(estimated_cost), 0) AS cost
            FROM llm_calls
            WHERE created_at >= datetime('now', '-{int(days)} days')
            GROUP BY DATE(created_at)
            ORDER BY day ASC
            """,
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    finally:
        conn.close()
    return [
        {"day": d, "calls": int(c or 0), "cost_usd": float(cost or 0.0)}
        for d, c, cost in rows
    ]


def top_runs_by_cost(
    n: int = 5,
    days: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Top N most expensive runs in the window. Useful for spotting outliers."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    where = _window_clause(days)
    try:
        rows = conn.execute(
            f"""
            SELECT workflow_run_id,
                   MIN(created_at)               AS started_at,
                   COUNT(*)                      AS calls,
                   COALESCE(SUM(tokens_input), 0)   AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)  AS tokens_out,
                   COALESCE(SUM(estimated_cost), 0) AS cost
            FROM llm_calls
            {where}
            GROUP BY workflow_run_id
            ORDER BY cost DESC
            LIMIT ?
            """,
            (int(n),),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    finally:
        conn.close()
    return [
        {
            "workflow_run_id": wf,
            "started_at": started,
            "calls": int(c or 0),
            "tokens_input": int(ti or 0),
            "tokens_output": int(to or 0),
            "cost_usd": float(cost or 0.0),
        }
        for wf, started, c, ti, to, cost in rows
    ]


def all_runs_by_cost(
    days: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Every run in the window, ordered by cost descending. Same shape as
    top_runs_by_cost but no LIMIT — used by the dashboard's full-list table."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    where = _window_clause(days)
    try:
        rows = conn.execute(
            f"""
            SELECT workflow_run_id,
                   MIN(created_at)               AS started_at,
                   COUNT(*)                      AS calls,
                   COALESCE(SUM(tokens_input), 0)   AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)  AS tokens_out,
                   COALESCE(SUM(estimated_cost), 0) AS cost
            FROM llm_calls
            {where}
            GROUP BY workflow_run_id
            ORDER BY cost DESC
            """,
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    finally:
        conn.close()
    return [
        {
            "workflow_run_id": wf,
            "started_at": started,
            "calls": int(c or 0),
            "tokens_input": int(ti or 0),
            "tokens_output": int(to or 0),
            "cost_usd": float(cost or 0.0),
        }
        for wf, started, c, ti, to, cost in rows
    ]


def top_calls_by_cost(
    n: int = 10,
    days: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Top N most expensive single LLM calls. Catches latency-tail outliers
    and individual prompts that ate a disproportionate share of the budget."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    where = _window_clause(days)
    try:
        rows = conn.execute(
            f"""
            SELECT workflow_run_id, agent_name, provider, model,
                   tokens_input, tokens_output, estimated_cost,
                   latency_ms, created_at
            FROM llm_calls
            {where}
            ORDER BY estimated_cost DESC
            LIMIT ?
            """,
            (int(n),),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    finally:
        conn.close()
    return [
        {
            "workflow_run_id": wf,
            "agent_name": a or "?",
            "provider": p or _provider_for_model(m),
            "model": m or "?",
            "tokens_input": int(ti or 0),
            "tokens_output": int(to or 0),
            "cost_usd": float(cost or 0.0),
            "latency_ms": int(lat or 0),
            "created_at": created,
        }
        for wf, a, p, m, ti, to, cost, lat, created in rows
    ]


def _empty_dashboard(days: int | None) -> dict:
    return {
        "window_days": days,
        "totals": {"calls": 0, "tokens_input": 0, "tokens_output": 0,
                   "cost_usd": 0.0, "distinct_runs": 0},
        "by_agent": [],
        "by_model": [],
    }
