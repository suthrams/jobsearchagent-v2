"""System Dashboard view - one operational pane across all runs (ADR-073).

Unifies the PSSR review axis plus Security and Cost: Security, Performance,
Reliability, Scalability, and the (former Cost Dashboard) Cost section, sharing
one time-window + profile control. Stored per run (correlation id), viewed
system-level and profile-scoped (ADR-062); supports a profile -> run -> job
drilldown (design doc Section 5.3).

Reads come from deterministic services - `system_health` (Security/Performance/
Reliability/Scalability/by-profile) and `cost_breakdown` (Cost) - so this view
stays a thin renderer (ui_architecture.md read-path).
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.services import system_health as sh
from app.services.cost_breakdown import (
    all_runs_by_cost,
    compute_dashboard_aggregate,
    daily_spend_trend,
    top_calls_by_cost,
    top_runs_by_cost,
)
from app.ui.formatting import _fmt_ts
from app.ui.nav import ViewContext, _navigate

_WINDOW_MAP = {"Last 7 days": 7, "Last 30 days": 30, "All time": None}


def render(ctx: ViewContext) -> None:
    st.header("System Dashboard")
    st.caption("Spend, security posture, performance, and reliability across all "
               "runs. Stored per run (correlation id), viewed system-level - "
               "scoped to the active profile unless you switch to all profiles.")

    # ── Shared controls ───────────────────────────────────────────────────────
    wc1, wc2 = st.columns([3, 2])
    with wc1:
        window_choice = st.radio(
            "Time window", list(_WINDOW_MAP.keys()),
            index=1, horizontal=True, label_visibility="collapsed",
        )
    with wc2:
        all_profiles = st.checkbox(
            "All profiles (system-wide)", value=False,
            help="Off: only the active profile. On: every profile, with a "
                 "by-profile breakdown you can drill into.",
        )
    window_days = _WINDOW_MAP[window_choice]

    # ── Profile drilldown resolution (design doc 5.3) ─────────────────────────
    # Precedence: explicit drill filter > all-profiles (None) > active profile.
    drill = st.session_state.get("dashboard_profile_filter")
    if drill is not None:
        view_uid: str | None = str(drill)
    elif all_profiles:
        view_uid = None
    else:
        view_uid = st.session_state.get("current_user_id")

    if view_uid is None:
        scope_label = "all profiles"
    elif drill is not None:
        scope_label = f"drilled into profile {view_uid}"
    else:
        scope_label = f"profile {view_uid}"
    st.caption(f"Window: {window_choice.lower()} - viewing: {scope_label}")

    # ── Headline metrics ──────────────────────────────────────────────────────
    cost_dash = compute_dashboard_aggregate(days=window_days, user_id=view_uid)
    totals = cost_dash["totals"]
    sec = sh.security_summary(days=window_days, user_id=view_uid)
    rel = sh.reliability_summary(days=window_days, user_id=view_uid)

    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Total spend", f"${totals['cost_usd']:.4f}")
    h2.metric("LLM calls", f"{totals['calls']:,}")
    h3.metric("Distinct runs", totals["distinct_runs"])
    sev = sec["by_severity"]
    h4.metric("Security events", sec["total"],
              delta=(f"{sev['high']} high - {sev['warning']} warning"
                     if sec["total"] else None),
              delta_color="inverse" if (sev["high"] or sev["warning"]) else "normal")
    h5.metric("Run success", f"{rel['success_rate'] * 100:.0f}%",
              delta=(f"{rel['runs_completed']}/{rel['runs_total']} ok"
                     if rel["runs_total"] else None))

    # ── By-profile breakdown (all-profiles mode only) ─────────────────────────
    if view_uid is None:
        _render_by_profile(window_days)

    # ── Drill breadcrumb ──────────────────────────────────────────────────────
    if drill is not None:
        _render_breadcrumb(str(drill))

    # ── Sections ──────────────────────────────────────────────────────────────
    _render_security(sec)
    _render_decisions(window_days, view_uid)
    _render_performance(window_days, view_uid)
    _render_reliability(rel)
    _render_scalability(window_days, view_uid)
    _render_cost(window_choice, window_days, view_uid, cost_dash)


# ── By-profile drilldown navigator ────────────────────────────────────────────


def _render_by_profile(window_days: int | None) -> None:
    st.markdown("---")
    st.subheader("By profile")
    st.caption("Click a row to drill into that profile - every section below "
               "re-scopes to it. The 'system / legacy' row holds run-less "
               "(sentinel) and pre-multiuser events; it is excluded from a "
               "specific profile's drilldown.")
    rows = sh.profiles_overview(days=window_days)
    if not rows:
        st.caption("No runs in this window.")
        return
    df = pd.DataFrame(rows)
    df["Security (h/w/i)"] = df.apply(
        lambda r: f"{r['sec_high']} / {r['sec_warning']} / {r['sec_info']}", axis=1
    )
    df["Success"] = (df["success_rate"] * 100).round(0).astype(int).astype(str) + "%"
    view = df[["name", "user_id", "runs", "spend_usd", "Security (h/w/i)", "Success"]].rename(
        columns={"name": "Profile", "user_id": "id", "runs": "Runs", "spend_usd": "Spend ($)"}
    )
    ev = st.dataframe(
        view, hide_index=True, use_container_width=True,
        on_select="rerun", selection_mode="single-row", key="sysdash_profiles_table",
        column_config={"Spend ($)": st.column_config.NumberColumn(format="$%.4f")},
    )
    selrows = (ev.selection.rows if ev and getattr(ev, "selection", None) else []) or []
    if selrows and selrows[0] < len(df):
        chosen = str(df.iloc[selrows[0]]["user_id"])
        if chosen != str(st.session_state.get("dashboard_profile_filter")):
            st.session_state.dashboard_profile_filter = chosen
            st.rerun()


def _render_breadcrumb(uid: str) -> None:
    st.markdown("---")
    c1, c2 = st.columns([4, 1])
    with c1:
        st.info(f"Drilled into profile id {uid}. All sections below are scoped to "
                "it; sentinel/legacy events are excluded.")
    with c2:
        if st.button("Clear filter", use_container_width=True, key="sysdash_clear_drill"):
            st.session_state.pop("dashboard_profile_filter", None)
            st.rerun()


# ── Security ──────────────────────────────────────────────────────────────────


def _render_security(sec: dict) -> None:
    st.markdown("---")
    st.subheader("Security events")
    st.caption("From the `security_events` audit table (ADR-073). Descriptions "
               "are PII-safe by construction: counts, field names, reason classes, "
               "hosts - never resume content.")
    if sec["total"] == 0:
        st.success("No security events recorded in this window.")
        return
    c1, c2 = st.columns(2)
    with c1:
        by_type = pd.DataFrame(sec["by_type"])
        if not by_type.empty:
            fig = px.bar(by_type.sort_values("count"), x="count", y="event_type",
                         orientation="h", text="count",
                         labels={"count": "Events", "event_type": "Type"},
                         color="count", color_continuous_scale="purples")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                              height=max(180, 40 * len(by_type)), coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        sv = sec["by_severity"]
        st.markdown(f"**Severity** - high: {sv['high']} - warning: {sv['warning']} "
                    f"- info: {sv['info']}")
    with c2:
        recent = pd.DataFrame(sec["recent"])
        if not recent.empty:
            recent["When"] = recent["created_at"].apply(_fmt_ts)
            recent["Run"] = recent["workflow_run_id"].apply(
                lambda s: (s[:8] + "...") if isinstance(s, str) and len(s) > 8 else s)
            tbl = recent[["When", "event_type", "severity", "Run", "description"]].rename(
                columns={"event_type": "Type", "severity": "Sev", "description": "Detail"})
            st.dataframe(tbl, hide_index=True, use_container_width=True, height=300)


# ── Decisions (governance / accountability, ADR-074) ─────────────────────────


def _render_decisions(window_days: int | None, view_uid: str | None) -> None:
    dec = sh.decisions_summary(days=window_days, user_id=view_uid)
    st.markdown("---")
    st.subheader("Human decisions")
    st.caption("Every approve / revise / reject / edit on a tailoring or clinic "
               "draft, from the `human_decisions` audit trail (ADR-074). The "
               "accountable human is the final author (ADR-059); this is the "
               "unified record of who decided what.")
    if dec["total"] == 0:
        st.caption("No human decisions recorded in this window.")
        return
    c1, c2 = st.columns(2)
    with c1:
        by_val = dec["by_value"]
        st.markdown("**By decision** - " + " - ".join(
            f"{k}: {v}" for k, v in sorted(by_val.items())))
        by_type = dec["by_type"]
        st.markdown("**By artifact** - " + " - ".join(
            f"{k}: {v}" for k, v in sorted(by_type.items())))
    with c2:
        recent = pd.DataFrame(dec["recent"])
        if not recent.empty:
            recent["When"] = recent["decided_at"].apply(_fmt_ts)
            recent["Run"] = recent["workflow_run_id"].apply(
                lambda s: (s[:8] + "...") if isinstance(s, str) and len(s) > 8 else s)
            tbl = recent[["When", "decision_type", "decision_value", "Run"]].rename(
                columns={"decision_type": "Artifact", "decision_value": "Decision"})
            st.dataframe(tbl, hide_index=True, use_container_width=True, height=260)


# ── Performance ───────────────────────────────────────────────────────────────


def _render_performance(window_days: int | None, view_uid: str | None) -> None:
    perf = sh.performance_summary(days=window_days, user_id=view_uid)
    st.markdown("---")
    st.subheader("Performance")
    st.caption("Latency from the data already captured (`llm_calls.latency_ms`, "
               "`agent_events.duration_ms`). p50/p95 in seconds.")
    if perf["llm"]["calls"] == 0 and perf["agent"]["events"] == 0:
        st.caption("No latency data in this window.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM p50", f"{perf['llm']['p50_ms'] / 1000:.1f}s")
    c2.metric("LLM p95", f"{perf['llm']['p95_ms'] / 1000:.1f}s")
    c3.metric("Agent p50", f"{perf['agent']['p50_ms'] / 1000:.1f}s")
    c4.metric("Agent p95", f"{perf['agent']['p95_ms'] / 1000:.1f}s")
    slow = perf["slowest_agents"]
    if slow:
        sdf = pd.DataFrame(slow)
        sdf["p95_s"] = (sdf["p95_ms"] / 1000).round(2)
        fig = px.bar(sdf.sort_values("p95_s"), x="p95_s", y="agent_name", orientation="h",
                     text="p95_s", labels={"p95_s": "p95 (s)", "agent_name": "Agent"},
                     color="p95_s", color_continuous_scale="blues")
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                          height=max(180, 40 * len(sdf)), coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)


# ── Reliability ───────────────────────────────────────────────────────────────


def _render_reliability(rel: dict) -> None:
    st.markdown("---")
    st.subheader("Reliability")
    st.caption("Run terminal status (`workflow_runs`) + agent failures "
               "(`agent_events` status=failed). Failures degrade gracefully; the "
               "audit row records them.")
    c1, c2 = st.columns(2)
    with c1:
        m1, m2 = st.columns(2)
        m1.metric("Run success rate", f"{rel['success_rate'] * 100:.0f}%",
                  delta=f"{rel['runs_completed']}/{rel['runs_total']}")
        m2.metric("Agent failures", rel["agent_failures"])
        if rel["failures_by_agent"]:
            fdf = pd.DataFrame(rel["failures_by_agent"])
            fig = px.bar(fdf.sort_values("count"), x="count", y="agent_name",
                         orientation="h", text="count",
                         labels={"count": "Failures", "agent_name": "Agent"},
                         color="count", color_continuous_scale="reds")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                              height=max(140, 38 * len(fdf)), coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
    with c2:
        recent = rel["recent_failures"]
        if recent:
            rdf = pd.DataFrame(recent)
            rdf["When"] = rdf["created_at"].apply(_fmt_ts)
            rdf["Run"] = rdf["workflow_run_id"].apply(
                lambda s: (s[:8] + "...") if isinstance(s, str) and len(s) > 8 else s)
            tbl = rdf[["When", "agent_name", "Run", "output_summary"]].rename(
                columns={"agent_name": "Agent", "output_summary": "Error"})
            st.dataframe(tbl, hide_index=True, use_container_width=True, height=260)
        else:
            st.caption("No agent failures in this window.")


# ── Scalability (light) ───────────────────────────────────────────────────────


def _render_scalability(window_days: int | None, view_uid: str | None) -> None:
    scl = sh.scalability_summary(days=window_days, user_id=view_uid)
    st.markdown("---")
    st.subheader("Scalability")
    st.caption("Throughput. Deliberately light - a single-node SQLite app has "
               "little true scalability signal.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Avg jobs / run", scl["avg_jobs_per_run"])
    c2.metric("Runs / day", scl["runs_per_day"])
    c3.metric("Peak jobs in a run", scl["peak_jobs_in_run"])


# ── Cost (the former Cost Dashboard, refactored into a section) ────────────────


def _render_cost(window_choice: str, window_days: int | None,
                 view_uid: str | None, dash: dict) -> None:
    st.markdown("---")
    st.subheader("Cost")
    st.caption("System-wide LLM spend. Numbers come from the `llm_calls` audit "
               "table. See `docs/cost_troubleshooting.md` for the lever matrix.")
    totals = dash["totals"]
    if totals["calls"] == 0:
        st.info(f"No LLM calls recorded in the {window_choice.lower()} window for "
                "this scope.")
        return

    h5, h6 = st.columns(2)
    h5.metric("Tokens in", f"{totals['tokens_input']:,}")
    h6.metric("Tokens out", f"{totals['tokens_output']:,}")

    # Cache effectiveness
    cache_w = totals.get("cache_creation_tokens", 0)
    cache_r = totals.get("cache_read_tokens", 0)
    if cache_w or cache_r:
        st.markdown("**Cache effectiveness**")
        c1, c2, c3 = st.columns(3)
        hit_pct = totals.get("cache_hit_ratio", 0.0) * 100
        c1.metric("Cache-hit ratio", f"{hit_pct:.1f}%")
        c2.metric("Cache reads", f"{cache_r:,}")
        c3.metric("Cache writes", f"{cache_w:,}")
        if hit_pct < 30 and totals["calls"] >= 10:
            st.warning(f"Cache-hit ratio below 30% across {totals['calls']} calls - "
                       "prompts may be changing across calls, or runs are spaced "
                       "more than 5 minutes apart.")

    # Daily spend trend
    if window_days:
        trend_rows = daily_spend_trend(days=window_days, user_id=view_uid)
        if trend_rows:
            trend_df = pd.DataFrame(trend_rows)
            fig = px.line(trend_df, x="day", y="cost_usd", markers=True,
                          labels={"day": "Day", "cost_usd": "Cost ($)"})
            fig.update_traces(hovertemplate="<b>%{x}</b><br>$%{y:.4f}<extra></extra>")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=240)
            st.plotly_chart(fig, use_container_width=True)

    # Per-agent
    if dash["by_agent"]:
        st.markdown("**Per-agent cost**")
        ag_df = pd.DataFrame(dash["by_agent"]).sort_values("cost_usd", ascending=True)
        fig = px.bar(ag_df, x="cost_usd", y="agent_name", orientation="h", text="cost_usd",
                     labels={"cost_usd": "Cost ($)", "agent_name": "Agent"},
                     color="cost_usd", color_continuous_scale="oranges")
        fig.update_traces(texttemplate="$%{x:.4f}", textposition="outside")
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                          height=max(200, 34 * len(ag_df)), coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    # Drill-through tables tucked into an expander to keep the unified page tidy
    with st.expander("Top runs, all runs, and most expensive calls", expanded=False):
        _render_cost_tables(window_days, view_uid)

    with st.expander("Reconcile against the provider billing console", expanded=False):
        st.markdown(
            "The local total is computed from successful calls at our local rate "
            "table. Providers bill all calls including retries. A 10-20% gap is "
            "normal; more than 2x means something is wrong (see "
            "`docs/cost_troubleshooting.md` Step 4).\n\n"
            f"**Local total ({window_choice.lower()}): ${totals['cost_usd']:.4f}**"
        )


def _render_cost_tables(window_days: int | None, view_uid: str | None) -> None:
    runs = top_runs_by_cost(n=5, days=window_days, user_id=view_uid)
    if runs:
        st.markdown("**Top 5 most expensive runs** (click to open)")
        runs_df = pd.DataFrame(runs)
        runs_df["ID"] = runs_df["workflow_run_id"].apply(lambda s: (s[:8] + "...") if len(s) > 8 else s)
        runs_df["Started"] = runs_df["started_at"].apply(_fmt_ts)
        view = runs_df[["ID", "Started", "calls", "tokens_input", "tokens_output", "cost_usd"]].rename(
            columns={"calls": "Calls", "tokens_input": "Tokens in",
                     "tokens_output": "Tokens out", "cost_usd": "Cost ($)"})
        ev = st.dataframe(view, hide_index=True, use_container_width=True,
                          on_select="rerun", selection_mode="single-row",
                          key="sysdash_top_runs",
                          column_config={"Cost ($)": st.column_config.NumberColumn(format="$%.4f")})
        sel = (ev.selection.rows if ev and getattr(ev, "selection", None) else []) or []
        if sel and sel[0] < len(runs_df):
            chosen_wf = str(runs_df.iloc[sel[0]]["workflow_run_id"])
            if chosen_wf and chosen_wf != st.session_state.get("detail_workflow_id"):
                _navigate("Workflow Detail", detail_workflow_id=chosen_wf, detail_job_id=None)

    all_runs = all_runs_by_cost(days=window_days, user_id=view_uid)
    if all_runs:
        st.markdown(f"**All runs by cost** ({len(all_runs)} in window, "
                    f"sum ${sum(r['cost_usd'] for r in all_runs):.4f})")
        all_df = pd.DataFrame(all_runs)
        all_df["ID"] = all_df["workflow_run_id"].apply(lambda s: (s[:8] + "...") if len(s) > 8 else s)
        all_df["Started"] = all_df["started_at"].apply(_fmt_ts)
        view = all_df[["ID", "Started", "calls", "tokens_input", "tokens_output", "cost_usd"]].rename(
            columns={"calls": "Calls", "tokens_input": "Tokens in",
                     "tokens_output": "Tokens out", "cost_usd": "Cost ($)"})
        ev = st.dataframe(view, hide_index=True, use_container_width=True,
                          on_select="rerun", selection_mode="single-row",
                          key="sysdash_all_runs",
                          column_config={"Cost ($)": st.column_config.NumberColumn(format="$%.4f")})
        sel = (ev.selection.rows if ev and getattr(ev, "selection", None) else []) or []
        if sel and sel[0] < len(all_df):
            chosen_wf = str(all_df.iloc[sel[0]]["workflow_run_id"])
            if chosen_wf and chosen_wf != st.session_state.get("detail_workflow_id"):
                _navigate("Workflow Detail", detail_workflow_id=chosen_wf, detail_job_id=None)

    calls = top_calls_by_cost(n=10, days=window_days, user_id=view_uid)
    if calls:
        st.markdown("**Top 10 most expensive single calls**")
        calls_df = pd.DataFrame(calls)
        calls_df["Run"] = calls_df["workflow_run_id"].apply(lambda s: (s[:8] + "...") if len(s) > 8 else s)
        calls_df["When"] = calls_df["created_at"].apply(_fmt_ts)
        view = calls_df[["When", "Run", "agent_name", "provider", "model",
                         "tokens_input", "tokens_output", "cost_usd", "latency_ms"]].rename(
            columns={"agent_name": "Agent", "provider": "Provider", "model": "Model",
                     "tokens_input": "Tokens in", "tokens_output": "Tokens out",
                     "cost_usd": "Cost ($)", "latency_ms": "Latency (ms)"})
        st.dataframe(view, hide_index=True, use_container_width=True,
                     column_config={
                         "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                         "Latency (ms)": st.column_config.NumberColumn(format="%d ms")})
