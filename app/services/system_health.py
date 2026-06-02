"""System-health aggregations for the unified System Dashboard (ADR-073).

Mirrors `cost_breakdown.py`: pure, deterministic SQL reads over the observability
tables, all profile-scoped (ADR-062), returning plain dicts the Streamlit view
renders. No LLM, no side effects. One function group per dashboard section:

  - security_summary    -> Security section (counts by type/severity + recent)
  - performance_summary -> Performance section (latency p50/p95, slowest agents)
  - reliability_summary -> Reliability section (run success rate, agent failures)
  - scalability_summary -> Scalability section (throughput; deliberately light)
  - profiles_overview   -> the by-profile drilldown breakdown (all-profiles mode)

Profile scoping follows the Cost Dashboard contract: pass ``user_id=None`` for
all profiles, or a decimal-string id for one profile. Events/calls/scores carry
no user_id of their own — ownership is the user_id of the workflow_runs row their
workflow_run_id points at, COALESCEd to '0' for run-less (SYSTEM_RUN_ID sentinel)
and pre-ADR-062 orphan rows. ``days=None`` scans all-time.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from app.repositories.api_request_repository import ApiRequestRepository
from app.repositories.database import DEFAULT_DB_PATH
from app.repositories.decision_repository import DecisionRepository
from app.repositories.security_repository import SecurityRepository

# Severity buckets the UI renders in a fixed order (ADR-073 severity scale).
SEVERITIES = ("high", "warning", "info")


# ── helpers ──────────────────────────────────────────────────────────────────


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (pct in [0,1]). 0.0 for an empty list.

    SQLite has no percentile function, so we fetch the scoped values and compute
    here. Fine at this app's scale (hundreds-to-thousands of rows per window).
    """
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _run_scoped_clause(days: int | None, user_id: str | None, table: str) -> tuple[str, tuple]:
    """WHERE clause for a table carrying `workflow_run_id` (llm_calls,
    agent_events, security_events, job_scores). Scopes by time window and by the
    owning profile via a correlated subquery on workflow_runs (COALESCE to '0').
    """
    clauses: list[str] = []
    params: list = []
    if days is not None and days > 0:
        clauses.append(f"{table}.created_at >= datetime('now', '-{int(days)} days')")
    if user_id is not None:
        clauses.append(
            f"COALESCE((SELECT wr.user_id FROM workflow_runs wr "
            f"WHERE wr.id = {table}.workflow_run_id), '0') = ?"
        )
        params.append(str(user_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


def _runs_scoped_clause(days: int | None, user_id: str | None) -> tuple[str, tuple]:
    """WHERE clause for the workflow_runs table itself (has user_id directly)."""
    clauses: list[str] = []
    params: list = []
    if days is not None and days > 0:
        clauses.append(f"started_at >= datetime('now', '-{int(days)} days')")
    if user_id is not None:
        clauses.append("COALESCE(user_id, '0') = ?")
        params.append(str(user_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


def _connect(db_path: Path) -> sqlite3.Connection | None:
    if not Path(db_path).exists():
        return None
    return sqlite3.connect(str(db_path))


# ── Security ─────────────────────────────────────────────────────────────────


def security_summary(
    days: int | None = None,
    user_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    recent_n: int = 25,
) -> dict:
    """Security section rollup. Reuses SecurityRepository.list_for_user (so the
    COALESCE-to-'0' scoping lives in exactly one place) and aggregates in Python.

    Returns:
      {"total": int,
       "by_type": [{"event_type": str, "count": int}, ...],   # desc by count
       "by_severity": {"high": int, "warning": int, "info": int},
       "recent": [ {id, event_type, severity, description, created_at,
                    workflow_run_id, owner_user_id}, ... ]}    # newest first
    """
    rows = SecurityRepository(db_path).list_for_user(user_id=user_id, days=days)
    by_type: dict[str, int] = {}
    by_sev: dict[str, int] = {s: 0 for s in SEVERITIES}
    for r in rows:
        et = r.get("event_type") or "?"
        by_type[et] = by_type.get(et, 0) + 1
        sev = r.get("severity") or "info"
        by_sev[sev] = by_sev.get(sev, 0) + 1
    by_type_sorted = sorted(
        ({"event_type": k, "count": v} for k, v in by_type.items()),
        key=lambda d: d["count"], reverse=True,
    )
    return {
        "total": len(rows),
        "by_type": by_type_sorted,
        "by_severity": by_sev,
        "recent": rows[:recent_n],
    }


# ── Human decisions (ADR-074 Gap 1) ──────────────────────────────────────────


def decisions_summary(
    days: int | None = None,
    user_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    recent_n: int = 25,
) -> dict:
    """Governance rollup over the `human_decisions` audit trail (ADR-074 Gap 1).

    Reuses DecisionRepository.list_for_user (COALESCE-to-'0' scoping in one place)
    and aggregates in Python.

    Returns:
      {"total": int,
       "by_type": {decision_type: count},          # e.g. tailoring / resume_clinic
       "by_value": {decision_value: count},        # approve / revise / reject / edit
       "recent": [ {..., owner_user_id}, ... ]}     # newest first
    """
    rows = DecisionRepository(db_path).list_for_user(user_id=user_id, days=days)
    by_type: dict[str, int] = {}
    by_value: dict[str, int] = {}
    for r in rows:
        dt = r.get("decision_type") or "?"
        dv = r.get("decision_value") or "?"
        by_type[dt] = by_type.get(dt, 0) + 1
        by_value[dv] = by_value.get(dv, 0) + 1
    return {
        "total": len(rows),
        "by_type": by_type,
        "by_value": by_value,
        "recent": rows[:recent_n],
    }


# ── Performance ──────────────────────────────────────────────────────────────


def performance_summary(
    days: int | None = None,
    user_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    slowest_n: int = 6,
) -> dict:
    """Latency rollup from data already captured (llm_calls.latency_ms,
    agent_events.duration_ms). Percentiles computed in Python.

    Returns:
      {"llm": {"p50_ms", "p95_ms", "calls"},
       "agent": {"p50_ms", "p95_ms", "events"},
       "slowest_agents": [{"agent_name", "p95_ms", "events"}, ...]}  # desc by p95
    """
    empty = {"llm": {"p50_ms": 0.0, "p95_ms": 0.0, "calls": 0},
             "agent": {"p50_ms": 0.0, "p95_ms": 0.0, "events": 0},
             "slowest_agents": [], "slowest_steps": []}
    conn = _connect(db_path)
    if conn is None:
        return empty
    try:
        w_llm, p_llm = _run_scoped_clause(days, user_id, "llm_calls")
        llm_lat = [
            int(v) for (v,) in conn.execute(
                f"SELECT latency_ms FROM llm_calls {w_llm}", p_llm
            ).fetchall() if v is not None
        ]
        w_ev, p_ev = _run_scoped_clause(days, user_id, "agent_events")
        agent_rows = conn.execute(
            f"SELECT agent_name, duration_ms FROM agent_events {w_ev}", p_ev
        ).fetchall()
        # ADR-074 Gap 2: node-level step timing from step_executions.
        w_st, p_st = _run_scoped_clause(days, user_id, "step_executions")
        step_rows = conn.execute(
            f"SELECT step, duration_ms FROM step_executions {w_st}", p_st
        ).fetchall()
    except sqlite3.OperationalError:
        return empty
    finally:
        conn.close()

    agent_dur = [int(d) for (_a, d) in agent_rows if d is not None]
    per_agent: dict[str, list[float]] = {}
    for a, d in agent_rows:
        if d is None:
            continue
        per_agent.setdefault(a or "?", []).append(float(d))
    slowest = sorted(
        ({"agent_name": a, "p95_ms": _percentile(v, 0.95), "events": len(v)}
         for a, v in per_agent.items()),
        key=lambda d: d["p95_ms"], reverse=True,
    )[:slowest_n]

    per_step: dict[str, list[float]] = {}
    for s, d in step_rows:
        if d is None:
            continue
        per_step.setdefault(s or "?", []).append(float(d))
    slowest_steps = sorted(
        ({"step": s, "p95_ms": _percentile(v, 0.95), "executions": len(v)}
         for s, v in per_step.items()),
        key=lambda d: d["p95_ms"], reverse=True,
    )[:slowest_n]

    return {
        "llm": {"p50_ms": _percentile([float(x) for x in llm_lat], 0.50),
                "p95_ms": _percentile([float(x) for x in llm_lat], 0.95),
                "calls": len(llm_lat)},
        "agent": {"p50_ms": _percentile([float(x) for x in agent_dur], 0.50),
                  "p95_ms": _percentile([float(x) for x in agent_dur], 0.95),
                  "events": len(agent_dur)},
        "slowest_agents": slowest,
        "slowest_steps": slowest_steps,
    }


# ── Reliability ──────────────────────────────────────────────────────────────


def reliability_summary(
    days: int | None = None,
    user_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    recent_n: int = 10,
) -> dict:
    """Failure/retry signal from workflow_runs (terminal status) and agent_events
    (status='failed').

    Returns:
      {"runs_total", "runs_completed", "runs_failed", "success_rate",
       "agent_failures", "failures_by_agent": [{"agent_name","count"}...],
       "recent_failures": [{agent_name, workflow_run_id, output_summary, created_at}...]}
    """
    empty = {"runs_total": 0, "runs_completed": 0, "runs_failed": 0,
             "success_rate": 0.0, "agent_failures": 0,
             "failures_by_agent": [], "recent_failures": []}
    conn = _connect(db_path)
    if conn is None:
        return empty
    try:
        w_runs, p_runs = _runs_scoped_clause(days, user_id)
        status_rows = conn.execute(
            f"SELECT status, COUNT(*) FROM workflow_runs {w_runs} GROUP BY status",
            p_runs,
        ).fetchall()
        w_ev, p_ev = _run_scoped_clause(days, user_id, "agent_events")
        fail_where = (w_ev + " AND " if w_ev else "WHERE ") + "agent_events.status = 'failed'"
        by_agent = conn.execute(
            f"SELECT agent_name, COUNT(*) c FROM agent_events {fail_where} "
            f"GROUP BY agent_name ORDER BY c DESC", p_ev,
        ).fetchall()
        recent = conn.execute(
            f"SELECT agent_name, workflow_run_id, output_summary, created_at "
            f"FROM agent_events {fail_where} ORDER BY created_at DESC LIMIT ?",
            (*p_ev, int(recent_n)),
        ).fetchall()
    except sqlite3.OperationalError:
        return empty
    finally:
        conn.close()

    counts = {s: int(c or 0) for s, c in status_rows}
    total = sum(counts.values())
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    agent_failures = sum(int(c or 0) for _a, c in by_agent)
    return {
        "runs_total": total,
        "runs_completed": completed,
        "runs_failed": failed,
        "success_rate": (completed / total) if total else 0.0,
        "agent_failures": agent_failures,
        "failures_by_agent": [
            {"agent_name": a or "?", "count": int(c or 0)} for a, c in by_agent
        ],
        "recent_failures": [
            {"agent_name": a or "?", "workflow_run_id": wf,
             "output_summary": (o or "")[:200], "created_at": ts}
            for a, wf, o, ts in recent
        ],
    }


# ── API requests (ADR-074 Gap 5) ─────────────────────────────────────────────


def api_summary(
    days: int | None = None,
    user_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    slowest_n: int = 8,
) -> dict:
    """HTTP-layer rollup over api_requests (ADR-074 Gap 5).

    Reuses ApiRequestRepository.list_for_user (profile-scoped, COALESCE to '0')
    and aggregates in Python.

    Returns:
      {"total", "error_count" (status>=400), "error_rate", "p50_ms", "p95_ms",
       "by_endpoint": [{"route_template","method","count","errors","p95_ms"}...]}
    """
    rows = ApiRequestRepository(db_path).list_for_user(user_id=user_id, days=days)
    if not rows:
        return {"total": 0, "error_count": 0, "error_rate": 0.0,
                "p50_ms": 0.0, "p95_ms": 0.0, "by_endpoint": []}
    lat = [float(r.get("latency_ms") or 0) for r in rows]
    errors = sum(1 for r in rows if int(r.get("status_code") or 0) >= 400)
    per: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("method") or "?", r.get("route_template") or "?")
        slot = per.setdefault(key, {"lat": [], "errors": 0})
        slot["lat"].append(float(r.get("latency_ms") or 0))
        if int(r.get("status_code") or 0) >= 400:
            slot["errors"] += 1
    by_endpoint = sorted(
        ({"method": m, "route_template": rt, "count": len(v["lat"]),
          "errors": v["errors"], "p95_ms": _percentile(v["lat"], 0.95)}
         for (m, rt), v in per.items()),
        key=lambda d: d["count"], reverse=True,
    )[:slowest_n]
    return {
        "total": len(rows),
        "error_count": errors,
        "error_rate": errors / len(rows),
        "p50_ms": _percentile(lat, 0.50),
        "p95_ms": _percentile(lat, 0.95),
        "by_endpoint": by_endpoint,
    }


# ── Scalability (deliberately light on a single-node SQLite app) ─────────────


def scalability_summary(
    days: int | None = None,
    user_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    """Throughput strip: avg jobs scored per run, runs/day, and a peak-concurrency
    proxy (the most jobs scored in any single run). Light by design.

    Returns:
      {"avg_jobs_per_run", "runs_per_day", "peak_jobs_in_run", "distinct_runs"}
    """
    empty = {"avg_jobs_per_run": 0.0, "runs_per_day": 0.0,
             "peak_jobs_in_run": 0, "distinct_runs": 0}
    conn = _connect(db_path)
    if conn is None:
        return empty
    try:
        w_js, p_js = _run_scoped_clause(days, user_id, "job_scores")
        per_run = conn.execute(
            f"SELECT workflow_run_id, COUNT(*) c FROM job_scores {w_js} "
            f"GROUP BY workflow_run_id", p_js,
        ).fetchall()
    except sqlite3.OperationalError:
        return empty
    finally:
        conn.close()

    distinct_runs = len(per_run)
    counts = [int(c or 0) for _wf, c in per_run]
    total_jobs = sum(counts)
    avg_jobs = (total_jobs / distinct_runs) if distinct_runs else 0.0
    peak = max(counts) if counts else 0
    runs_per_day = (distinct_runs / days) if (days and days > 0) else float(distinct_runs)
    return {
        "avg_jobs_per_run": round(avg_jobs, 2),
        "runs_per_day": round(runs_per_day, 2),
        "peak_jobs_in_run": peak,
        "distinct_runs": distinct_runs,
    }


# ── By-profile breakdown (the drilldown navigator) ───────────────────────────


def profiles_overview(
    days: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """One row per profile for the all-profiles by-profile breakdown (ADR-073
    Section 5.3). Always system-wide (it IS the cross-profile view); the user
    drills into a row to scope the rest of the dashboard.

    Each row: {user_id, name, runs, spend_usd, sec_high, sec_warning, sec_info,
               runs_completed, success_rate}. The '0' bucket is labeled
               "system / legacy" — it holds run-less sentinel + pre-multiuser rows.
    """
    conn = _connect(db_path)
    if conn is None:
        return []
    try:
        w_runs, p_runs = _runs_scoped_clause(days, None)
        run_rows = conn.execute(
            f"""SELECT COALESCE(user_id,'0') uid, COUNT(*) runs,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed
                FROM workflow_runs {w_runs} GROUP BY COALESCE(user_id,'0')""",
            p_runs,
        ).fetchall()
        # spend per profile (llm_calls -> owning run's user_id)
        w_llm, p_llm = _run_scoped_clause(days, None, "llm_calls")
        spend_rows = conn.execute(
            f"""SELECT COALESCE((SELECT wr.user_id FROM workflow_runs wr
                                 WHERE wr.id = llm_calls.workflow_run_id),'0') uid,
                       COALESCE(SUM(estimated_cost),0) cost
                FROM llm_calls {w_llm} GROUP BY uid""",
            p_llm,
        ).fetchall()
        user_rows = conn.execute("SELECT id, name FROM users").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    names = {str(i): n for i, n in user_rows}
    spend = {uid: float(c or 0.0) for uid, c in spend_rows}

    # security counts per profile reuse the repo scoping (one source of truth)
    sec_repo = SecurityRepository(db_path)

    out: list[dict] = []
    for uid, runs, completed in run_rows:
        uid = str(uid)
        sec = sec_repo.list_for_user(user_id=uid, days=days)
        sev = {s: 0 for s in SEVERITIES}
        for r in sec:
            sv = r.get("severity") or "info"
            sev[sv] = sev.get(sv, 0) + 1
        label = names.get(uid) or f"profile {uid}"
        out.append({
            "user_id": uid,
            "name": label,
            "runs": int(runs or 0),
            "spend_usd": spend.get(uid, 0.0),
            "sec_high": sev["high"],
            "sec_warning": sev["warning"],
            "sec_info": sev["info"],
            "runs_completed": int(completed or 0),
            "success_rate": (int(completed or 0) / int(runs)) if runs else 0.0,
        })
    out.sort(key=lambda d: d["spend_usd"], reverse=True)
    return out
