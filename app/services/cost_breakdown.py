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
