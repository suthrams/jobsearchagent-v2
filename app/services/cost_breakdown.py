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
                   COALESCE(SUM(cache_creation_tokens), 0) AS cache_w,
                   COALESCE(SUM(cache_read_tokens), 0)     AS cache_r,
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
    for agent_name, model, calls, t_in, t_out, cache_w, cache_r, cost, avg_latency in rows:
        cw = int(cache_w or 0)
        cr = int(cache_r or 0)
        out_rows.append({
            "agent_name": agent_name or "?",
            "provider": _provider_for_model(model),
            "model": model or "?",
            "calls": int(calls or 0),
            "tokens_input": int(t_in or 0),
            "tokens_output": int(t_out or 0),
            "cache_creation_tokens": cw,
            "cache_read_tokens": cr,
            "cache_hit_ratio": _cache_hit_ratio(int(t_in or 0), cw, cr),
            "cost_usd": float(cost or 0.0),
            "avg_latency_ms": float(avg_latency or 0.0),
        })

    agg = {
        "calls": sum(r["calls"] for r in out_rows),
        "tokens_input": sum(r["tokens_input"] for r in out_rows),
        "tokens_output": sum(r["tokens_output"] for r in out_rows),
        "cache_creation_tokens": sum(r["cache_creation_tokens"] for r in out_rows),
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in out_rows),
        "cost_usd": sum(r["cost_usd"] for r in out_rows),
        "avg_latency_ms": (
            sum(r["avg_latency_ms"] * r["calls"] for r in out_rows)
            / sum(r["calls"] for r in out_rows)
        ) if out_rows else 0.0,
    }
    agg["cache_hit_ratio"] = _cache_hit_ratio(
        agg["tokens_input"], agg["cache_creation_tokens"], agg["cache_read_tokens"]
    )
    return {"rows": out_rows, "aggregate": agg}


def _cache_hit_ratio(tokens_input: int, cache_creation: int, cache_read: int) -> float:
    """Cache reads as a fraction of total billable input tokens.

    `tokens_input` here is the union of regular + cache_creation + cache_read
    (the BaseAgent contract). Returns 0.0 when no input tokens were billed.
    A value > 0.5 means most of your input is being served from cache, which
    is the goal. A persistently low value means caching is configured but not
    landing — check that prompts are stable across calls within the 5-minute
    window.
    """
    if tokens_input <= 0:
        return 0.0
    return max(0.0, min(1.0, cache_read / tokens_input))


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
        "cache_creation_tokens": 0, "cache_read_tokens": 0,
        "cache_hit_ratio": 0.0,
        "cost_usd": 0.0, "avg_latency_ms": 0.0,
    }


# ── Cross-run aggregations (Cost Dashboard view) ─────────────────────────────
# These power the system-wide Cost Dashboard. They mirror the Step 5/8 SQL in
# docs/cost_troubleshooting.md so the UI surfaces the same numbers a developer
# would compute by hand. Pure functions over llm_calls, no side effects.
#
# All time-window arguments are in days. Pass days=None to scan all-time.


def _scope_clause(days: int | None, user_id: str | None) -> tuple[str, tuple]:
    """Build a combined WHERE clause for an `llm_calls` query, scoping by time
    window and (ADR-062) owning profile.

    llm_calls carries no user_id; ownership is the user_id of the workflow_runs
    row its workflow_run_id points at. A correlated subquery resolves it and
    COALESCEs orphan calls (no workflow_runs row — e.g. ad-hoc resume parses) to
    '0', so they count toward the default profile and never toward a new one.
    user_id=None means no profile filter (all-time, all profiles).
    """
    clauses: list[str] = []
    params: list = []
    if days is not None and days > 0:
        clauses.append(f"created_at >= datetime('now', '-{int(days)} days')")
    if user_id is not None:
        clauses.append(
            "COALESCE((SELECT wr.user_id FROM workflow_runs wr "
            "WHERE wr.id = llm_calls.workflow_run_id), '0') = ?"
        )
        params.append(str(user_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


def compute_dashboard_aggregate(
    days: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    user_id: str | None = None,
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
    where, sp = _scope_clause(days, user_id)
    try:
        totals_row = conn.execute(
            f"""
            SELECT COUNT(*)                                      AS calls,
                   COALESCE(SUM(tokens_input), 0)                AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)               AS tokens_out,
                   COALESCE(SUM(cache_creation_tokens), 0)       AS cache_w,
                   COALESCE(SUM(cache_read_tokens), 0)           AS cache_r,
                   COALESCE(SUM(estimated_cost), 0)              AS cost,
                   COUNT(DISTINCT workflow_run_id)               AS distinct_runs
            FROM llm_calls
            {where}
            """,
            sp,
        ).fetchone()
        agent_rows = conn.execute(
            f"""
            SELECT agent_name,
                   COUNT(*)                                      AS calls,
                   COALESCE(SUM(tokens_input), 0)                AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)               AS tokens_out,
                   COALESCE(SUM(cache_creation_tokens), 0)       AS cache_w,
                   COALESCE(SUM(cache_read_tokens), 0)           AS cache_r,
                   COALESCE(SUM(estimated_cost), 0)              AS cost
            FROM llm_calls
            {where}
            GROUP BY agent_name
            ORDER BY cost DESC
            """,
            sp,
        ).fetchall()
        model_rows = conn.execute(
            f"""
            SELECT model,
                   COUNT(*)                                      AS calls,
                   COALESCE(SUM(tokens_input), 0)                AS tokens_in,
                   COALESCE(SUM(tokens_output), 0)               AS tokens_out,
                   COALESCE(SUM(cache_creation_tokens), 0)       AS cache_w,
                   COALESCE(SUM(cache_read_tokens), 0)           AS cache_r,
                   COALESCE(SUM(estimated_cost), 0)              AS cost
            FROM llm_calls
            {where}
            GROUP BY model
            ORDER BY cost DESC
            """,
            sp,
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return _empty_dashboard(days)
    finally:
        conn.close()

    totals_cache_w = int(totals_row[3] or 0)
    totals_cache_r = int(totals_row[4] or 0)
    totals_tokens_in = int(totals_row[1] or 0)

    return {
        "window_days": days,
        "totals": {
            "calls":         int(totals_row[0] or 0),
            "tokens_input":  totals_tokens_in,
            "tokens_output": int(totals_row[2] or 0),
            "cache_creation_tokens": totals_cache_w,
            "cache_read_tokens": totals_cache_r,
            "cache_hit_ratio": _cache_hit_ratio(totals_tokens_in, totals_cache_w, totals_cache_r),
            "cost_usd":      float(totals_row[5] or 0.0),
            "distinct_runs": int(totals_row[6] or 0),
        },
        "by_agent": [
            {"agent_name": a or "?", "calls": int(c or 0),
             "tokens_input": int(ti or 0), "tokens_output": int(to or 0),
             "cache_creation_tokens": int(cw or 0),
             "cache_read_tokens": int(cr or 0),
             "cache_hit_ratio": _cache_hit_ratio(int(ti or 0), int(cw or 0), int(cr or 0)),
             "cost_usd": float(cost or 0.0)}
            for a, c, ti, to, cw, cr, cost in agent_rows
        ],
        "by_model": [
            {"model": m or "?", "provider": _provider_for_model(m),
             "calls": int(c or 0),
             "tokens_input": int(ti or 0), "tokens_output": int(to or 0),
             "cache_creation_tokens": int(cw or 0),
             "cache_read_tokens": int(cr or 0),
             "cache_hit_ratio": _cache_hit_ratio(int(ti or 0), int(cw or 0), int(cr or 0)),
             "cost_usd": float(cost or 0.0)}
            for m, c, ti, to, cw, cr, cost in model_rows
        ],
    }


def daily_spend_trend(
    days: int = 30,
    db_path: Path = DEFAULT_DB_PATH,
    user_id: str | None = None,
) -> list[dict]:
    """One row per day for the last N days. Days with zero spend are omitted —
    the UI fills in zeros if needed."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    where, sp = _scope_clause(days, user_id)
    try:
        rows = conn.execute(
            f"""
            SELECT DATE(created_at)              AS day,
                   COUNT(*)                      AS calls,
                   COALESCE(SUM(estimated_cost), 0) AS cost
            FROM llm_calls
            {where}
            GROUP BY DATE(created_at)
            ORDER BY day ASC
            """,
            sp,
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
    user_id: str | None = None,
) -> list[dict]:
    """Top N most expensive runs in the window. Useful for spotting outliers."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    where, sp = _scope_clause(days, user_id)
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
            (*sp, int(n)),
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
    user_id: str | None = None,
) -> list[dict]:
    """Every run in the window, ordered by cost descending. Same shape as
    top_runs_by_cost but no LIMIT — used by the dashboard's full-list table."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    where, sp = _scope_clause(days, user_id)
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
            sp,
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
    user_id: str | None = None,
) -> list[dict]:
    """Top N most expensive single LLM calls. Catches latency-tail outliers
    and individual prompts that ate a disproportionate share of the budget."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    where, sp = _scope_clause(days, user_id)
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
            (*sp, int(n)),
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
                   "cache_creation_tokens": 0, "cache_read_tokens": 0,
                   "cache_hit_ratio": 0.0,
                   "cost_usd": 0.0, "distinct_runs": 0},
        "by_agent": [],
        "by_model": [],
    }
