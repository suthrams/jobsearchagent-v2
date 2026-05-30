"""Cost Dashboard view - system-wide LLM spend across all runs.

Phase 4 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.services.cost_breakdown import (
    all_runs_by_cost,
    compute_dashboard_aggregate,
    daily_spend_trend,
    top_calls_by_cost,
    top_runs_by_cost,
)
from app.ui.formatting import _fmt_ts
from app.ui.nav import ViewContext, _navigate


def render(ctx: ViewContext) -> None:
    st.header("💰 Cost Dashboard")
    st.caption("System-wide LLM spend across all runs. The numbers here come from the "
               "`llm_calls` audit table. See `docs/cost_troubleshooting.md` for the "
               "lever decision matrix and reconciliation guide.")

    wc1, wc2 = st.columns([3, 2])
    with wc1:
        window_choice = st.radio(
            "Time window",
            ["Last 7 days", "Last 30 days", "All time"],
            horizontal=True, label_visibility="collapsed",
        )
    with wc2:
        # ADR-062: spend is attributable per profile. Default to this profile;
        # tick to see system-wide spend across every profile.
        all_profiles = st.checkbox(
            "All profiles (system-wide)", value=False,
            help="Off: only the active profile's runs. On: every profile.",
        )
    window_map = {"Last 7 days": 7, "Last 30 days": 30, "All time": None}
    window_days = window_map[window_choice]
    cost_uid = None if all_profiles else st.session_state.current_user_id

    dash = compute_dashboard_aggregate(days=window_days, user_id=cost_uid)
    totals = dash["totals"]

    if totals["calls"] == 0:
        st.info(
            f"No LLM calls recorded in the **{window_choice.lower()}** window.\n\n"
            "Either no workflows have run in this window, or observability isn't "
            "writing rows. Run `pytest tests/v2/test_cost_invariants.py -v` to verify "
            "the audit trail is intact."
        )
        st.stop()

    # ── Headline metrics ──────────────────────────────────────────────────────
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Total spend", f"${totals['cost_usd']:.4f}")
    h2.metric("LLM calls", f"{totals['calls']:,}")
    h3.metric("Distinct runs", totals["distinct_runs"])
    avg_per_run = (totals["cost_usd"] / totals["distinct_runs"]) if totals["distinct_runs"] else 0.0
    h4.metric("Avg cost / run", f"${avg_per_run:.4f}")
    h5, h6 = st.columns(2)
    h5.metric("Tokens in",  f"{totals['tokens_input']:,}")
    h6.metric("Tokens out", f"{totals['tokens_output']:,}")

    # ── Cache effectiveness ───────────────────────────────────────────────────
    # Anthropic ephemeral prompt cache: writes bill 1.25x input rate, reads 0.10x.
    # A persistently low hit ratio means caching is configured but not landing —
    # the prompt is changing across calls within the 5-min cache window.
    cache_w = totals.get("cache_creation_tokens", 0)
    cache_r = totals.get("cache_read_tokens", 0)
    if cache_w or cache_r:
        st.markdown("---")
        st.subheader("⚡ Cache effectiveness")
        st.caption("Reads served from prompt cache pay 10% of the input rate; "
                   "writes pay 125%. A high read ratio is the goal.")
        c1, c2, c3 = st.columns(3)
        hit_pct = totals.get("cache_hit_ratio", 0.0) * 100
        c1.metric("Cache-hit ratio", f"{hit_pct:.1f}%",
                  help="Cache reads as a % of total billable input tokens.")
        c2.metric("Cache reads",  f"{cache_r:,}",
                  help="Tokens served from cache at 0.10x the input rate.")
        c3.metric("Cache writes", f"{cache_w:,}",
                  help="Tokens written into cache at 1.25x the input rate.")
        if hit_pct < 30 and totals["calls"] >= 10:
            st.warning(
                "Cache-hit ratio is below 30% across "
                f"{totals['calls']} calls. Either prompts are changing across "
                "calls (check that the resume profile is stable and that no "
                "per-call timestamps leak into the system message), or runs "
                "are spaced more than 5 minutes apart so the ephemeral cache "
                "expires before reuse."
            )

    # ── Daily spend trend ─────────────────────────────────────────────────────
    if window_days:
        st.markdown("---")
        st.subheader("📈 Daily spend trend")
        trend_rows = daily_spend_trend(days=window_days, user_id=cost_uid)
        if trend_rows:
            trend_df = pd.DataFrame(trend_rows)
            fig = px.line(
                trend_df, x="day", y="cost_usd", markers=True,
                labels={"day": "Day", "cost_usd": "Cost ($)"},
                title=None,
            )
            fig.update_traces(hovertemplate="<b>%{x}</b><br>$%{y:.4f}<extra></extra>")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=260)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No daily activity in this window.")

    # ── Per-agent cost breakdown ──────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🤖 Per-agent cost breakdown")
    st.caption("Which agents are eating the budget. The biggest single bar is usually "
               "the right place to start cutting. Move it to a cheaper model in "
               "**Settings → Agent Models** if quality permits.")
    if dash["by_agent"]:
        ag_df = pd.DataFrame(dash["by_agent"]).sort_values("cost_usd", ascending=True)
        fig = px.bar(
            ag_df, x="cost_usd", y="agent_name", orientation="h",
            text="cost_usd",
            labels={"cost_usd": "Cost ($)", "agent_name": "Agent"},
            color="cost_usd", color_continuous_scale="oranges",
        )
        fig.update_traces(texttemplate="$%{x:.4f}", textposition="outside")
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                          height=max(220, 36 * len(ag_df)),
                          coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

        # Numeric table alongside the chart
        ag_table = pd.DataFrame(dash["by_agent"])
        ag_table["share_pct"] = (
            ag_table["cost_usd"] / max(totals["cost_usd"], 1e-9) * 100
        ).round(1)
        ag_table = ag_table.rename(columns={
            "agent_name": "Agent", "calls": "Calls",
            "tokens_input": "Tokens in", "tokens_output": "Tokens out",
            "cost_usd": "Cost ($)", "share_pct": "% of total",
        })
        st.dataframe(
            ag_table, hide_index=True, use_container_width=True,
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                "% of total": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

    # ── Per-model cost breakdown ──────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🧠 Per-model cost breakdown")
    st.caption("Sonnet vs Haiku vs gpt-4o — same call count on different models can "
               "differ by 10-25x in cost. See the rate table in `docs/cost_troubleshooting.md` "
               "Step 6.")
    if dash["by_model"]:
        m_df = pd.DataFrame(dash["by_model"])
        m_df["model_label"] = m_df["provider"] + " / " + m_df["model"]
        # Pie for cost share, bar for call count side by side.
        c1, c2 = st.columns(2)
        with c1:
            fig_pie = px.pie(
                m_df, values="cost_usd", names="model_label",
                title="Cost share by model", hole=0.4,
            )
            fig_pie.update_traces(textinfo="percent+label",
                                  hovertemplate="<b>%{label}</b><br>$%{value:.4f}<extra></extra>")
            fig_pie.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=320)
            st.plotly_chart(fig_pie, use_container_width=True)
        with c2:
            m_sorted = m_df.sort_values("calls", ascending=True)
            fig_bar = px.bar(
                m_sorted, x="calls", y="model_label", orientation="h",
                title="Call count by model",
                labels={"calls": "Calls", "model_label": "Model"},
                color="provider",
            )
            fig_bar.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=320,
                                  showlegend=False)
            st.plotly_chart(fig_bar, use_container_width=True)

    # ── Top 5 most expensive runs (drill-through) ─────────────────────────────
    st.markdown("---")
    st.subheader("🔥 Top 5 most expensive runs")
    st.caption("Click a row to open that run's full detail (per-job pipeline, per-agent "
               "cost, fidelity flags).")
    runs = top_runs_by_cost(n=5, days=window_days, user_id=cost_uid)
    if runs:
        runs_df = pd.DataFrame(runs)
        runs_df["ID"] = runs_df["workflow_run_id"].apply(lambda s: (s[:8] + "…") if len(s) > 8 else s)
        runs_df["Started"] = runs_df["started_at"].apply(_fmt_ts)
        runs_view = runs_df[["ID", "Started", "calls", "tokens_input", "tokens_output", "cost_usd"]]
        runs_view = runs_view.rename(columns={
            "calls": "Calls", "tokens_input": "Tokens in",
            "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
        })
        ev_runs = st.dataframe(
            runs_view, hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="single-row",
            key="cost_top_runs_table",
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
            },
        )
        sel = (ev_runs.selection.rows if ev_runs and getattr(ev_runs, "selection", None) else []) or []
        if sel and sel[0] < len(runs_df):
            chosen_wf = str(runs_df.iloc[sel[0]]["workflow_run_id"])
            if chosen_wf and chosen_wf != st.session_state.get("detail_workflow_id"):
                _navigate("Workflow Detail",
                          detail_workflow_id=chosen_wf, detail_job_id=None)

    # ── All runs by cost (full per-run table) ─────────────────────────────────
    st.markdown("---")
    st.subheader("📋 All runs by cost")
    st.caption("Every workflow in the selected window, sourced from `llm_calls` "
               "(the truth source — see `docs/cost_troubleshooting.md` Step 4 for "
               "why this differs from `state_json` estimates). Click a row to drill in.")
    all_runs = all_runs_by_cost(days=window_days, user_id=cost_uid)
    if all_runs:
        all_df = pd.DataFrame(all_runs)
        all_df["ID"] = all_df["workflow_run_id"].apply(lambda s: (s[:8] + "…") if len(s) > 8 else s)
        all_df["Started"] = all_df["started_at"].apply(_fmt_ts)
        all_view = all_df[["ID", "Started", "calls", "tokens_input", "tokens_output", "cost_usd"]]
        all_view = all_view.rename(columns={
            "calls": "Calls", "tokens_input": "Tokens in",
            "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
        })
        ev_all_runs = st.dataframe(
            all_view, hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="single-row",
            key="cost_all_runs_table",
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
            },
        )
        sel = (ev_all_runs.selection.rows if ev_all_runs and getattr(ev_all_runs, "selection", None) else []) or []
        if sel and sel[0] < len(all_df):
            chosen_wf = str(all_df.iloc[sel[0]]["workflow_run_id"])
            if chosen_wf and chosen_wf != st.session_state.get("detail_workflow_id"):
                _navigate("Workflow Detail",
                          detail_workflow_id=chosen_wf, detail_job_id=None)
        st.caption(f"{len(all_runs)} run(s) in this window. Sum: ${sum(r['cost_usd'] for r in all_runs):.4f}")

    # ── Top 10 most expensive single calls ────────────────────────────────────
    st.markdown("---")
    st.subheader("⚡ Top 10 most expensive single calls")
    st.caption("Outliers worth looking at — high token count, high latency, or both. A single "
               "call >$0.10 usually means a long prompt + Sonnet/Opus combination.")
    calls = top_calls_by_cost(n=10, days=window_days, user_id=cost_uid)
    if calls:
        calls_df = pd.DataFrame(calls)
        calls_df["Run"] = calls_df["workflow_run_id"].apply(lambda s: (s[:8] + "…") if len(s) > 8 else s)
        calls_df["When"] = calls_df["created_at"].apply(_fmt_ts)
        calls_view = calls_df[[
            "When", "Run", "agent_name", "provider", "model",
            "tokens_input", "tokens_output", "cost_usd", "latency_ms",
        ]].rename(columns={
            "agent_name": "Agent", "provider": "Provider", "model": "Model",
            "tokens_input": "Tokens in", "tokens_output": "Tokens out",
            "cost_usd": "Cost ($)", "latency_ms": "Latency (ms)",
        })
        st.dataframe(
            calls_view, hide_index=True, use_container_width=True,
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                "Latency (ms)": st.column_config.NumberColumn(format="%d ms"),
            },
        )

    # ── Reconciliation strip ──────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("🧮 Reconcile against the provider billing console", expanded=False):
        st.markdown(
            "The local total above is computed from successful calls at our local "
            "rate table. Anthropic / OpenAI bill for **all** calls including retries "
            "and may apply different rates for cached input. A 10-20% gap is normal; "
            "more than 2x means something's wrong (see "
            "`docs/cost_troubleshooting.md` Step 4).\n\n"
            f"**Local total ({window_choice.lower()}): ${totals['cost_usd']:.4f}**"
        )
        provider_total = st.number_input(
            "Provider console total for the same window (USD)",
            min_value=0.0, step=0.01, value=0.0, format="%.2f",
            help="Look this up in console.anthropic.com → Usage or "
                 "platform.openai.com → Usage and paste it here.",
        )
        if provider_total > 0:
            gap = provider_total - totals["cost_usd"]
            gap_pct = (gap / max(totals["cost_usd"], 1e-9)) * 100
            if abs(gap_pct) > 100:
                st.error(f"Gap: ${gap:+.4f} ({gap_pct:+.0f}%) — investigate. "
                         "Likely retries or stuck workflows; see Step 4 of the cost guide.")
            elif abs(gap_pct) > 20:
                st.warning(f"Gap: ${gap:+.4f} ({gap_pct:+.0f}%) — outside the normal "
                           "10-20% band. Worth a closer look.")
            else:
                st.success(f"Gap: ${gap:+.4f} ({gap_pct:+.0f}%) — within the normal range.")
