"""Search detail view - the per-run summary (internal name "Workflow Detail").

ADR-088 Tier 2 (Phase 6) shrank this to a RUN-level view: status + metrics, the
manual-selection picker (ADR-060), the Find & Score jobs table (each row opens the
Opportunity page), the discovered-jobs table, and collapsed Diagnostics. The per-job
Review / interview Prep / Tailoring action region moved to views/opportunity.py, so
the per-job work lives on one surface instead of being split across two screens.
All st.* calls run inside render().
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import app.ui.api_client as api
from app.services.constraint_analyzer import analyze, summary_metrics
from app.ui.data import (
    _cached_cost_breakdown,
    _cached_review_later,
    _cached_run_metrics,
    _cached_step_executions,
    _cached_workflow_detail,
    _cached_workflow_jobs,
)
from app.ui.formatting import (
    _checked,
    build_discovered_rows,
    build_relevance_drop_rows,
    discovery_funnel_summary,
    format_posting_age,
    format_posting_age_short,
)
from app.ui.nav import ViewContext, _navigate, back_button
from app.workflows.limits import TRACK_TO_SCORE_KEY, get_active_tracks


def render(ctx: ViewContext) -> None:
    back_button("Workflow History")  # in-app Back to Searches (ADR-088 F)
    st.header("Search detail")

    # Sync the input widget to the navigation target on actual nav changes (row
    # click in History or sidebar button) but preserve user typing across reruns.
    # Why: st.text_input without a key holds onto its old widget value and ignores
    # the new value= arg, so a fresh detail_workflow_id from _navigate would be
    # clobbered back to whatever was in the input on the previous render.
    nav_target = st.session_state.detail_workflow_id or st.session_state.workflow_id or ""
    if nav_target and st.session_state.get("_detail_wf_synced") != nav_target:
        st.session_state.detail_wf_input = nav_target
        st.session_state._detail_wf_synced = nav_target

    wf_id = st.text_input("Workflow ID", key="detail_wf_input",
                          help="Pick a run from Searches or paste an ID.")
    if not wf_id:
        st.info("No workflow selected.")
        st.stop()
    st.session_state.detail_workflow_id = wf_id

    record = _cached_workflow_detail(wf_id)
    state = (record or {}).get("state") or {}
    status = (record or {}).get("status", "unknown")

    # Status header
    icon = {"running": "🔵", "completed": "🟢", "failed": "🔴", "completed_with_errors": "🟠",
            "awaiting_scoring_selection": "🟡"}.get(status, "⚪")
    h1, h2 = st.columns([3, 1])
    h1.markdown(f"### {icon} `{status}`")
    h2.caption(f"Started: {(record or {}).get('started_at', '—')}")

    # Report + Live activity are click-through destinations (ADR-088); surface their
    # entries here so any run's report and live-style activity feed are reachable, not
    # just the active one. Both read st.session_state.workflow_id, so adopt this run
    # before navigating. The Live monitor is otherwise only reachable from the active-run
    # widget, so this is the path back to it once a run has finished.
    b1, b2 = st.columns(2)
    if status in ("completed", "completed_with_errors"):
        if b1.button("📄 View run report", key=f"open_report_{wf_id}"):
            _navigate("Run Report", workflow_id=wf_id, last_status=status)
    if b2.button("📡 Open live activity view", key=f"open_live_{wf_id}",
                 help="Step + agent-event activity feed (auto-refreshes while a run is active)."):
        _navigate("Live Run Monitor", workflow_id=wf_id, last_status=status)

    metrics = summary_metrics(state)
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Discovered", metrics["jobs_discovered"])
    g2.metric("Scored", metrics["jobs_scored"])
    g3.metric("Selected (auto)", metrics["jobs_selected"])
    g4.metric("LLM cost", f"${metrics['estimated_cost_usd']:.4f}")
    g5, g6, g7, g8 = st.columns(4)
    g5.metric("LLM calls", metrics["llm_calls"])
    g6.metric("Tokens in", f"{metrics['tokens_input']:,}")
    g7.metric("Tokens out", f"{metrics['tokens_output']:,}")
    g8.metric("Review rounds", metrics["review_rounds"])

    # Quick-look job-details modal, shared by every jobs table on this page
    # (manual-selection picker, scored table, discovered table). Descriptions
    # already live in state (normalized_jobs / scored_jobs carry job_description),
    # so no extra read is needed.
    _desc_by_id: dict = {}
    for _j in (state.get("scored_jobs") or []) + (state.get("normalized_jobs") or []):
        _jid = _j.get("job_id") or _j.get("id")
        if _jid and _j.get("job_description") and _jid not in _desc_by_id:
            _desc_by_id[_jid] = _j.get("job_description")

    @st.dialog("Job details", width="large")
    def _show_job(job: dict, desc: str | None) -> None:
        st.markdown(f"### {job.get('title') or '(untitled)'}")
        _bits = [job.get("company") or "—", job.get("location") or "—"]
        _age = format_posting_age(job.get("posted_at"))
        if _age:
            _bits.append(_age)
        st.caption("  ·  ".join(_bits))
        if job.get("url"):
            st.link_button("🔗 Open the full posting ↗", job["url"])
        _status = str(job.get("_status") or "")
        if job.get("overall_score") is not None or _status.startswith("✅"):
            _msg = "Scored"
            if job.get("overall_score") is not None:
                _msg += f" — overall {int(job['overall_score'])}"
            if job.get("match_summary"):
                _msg += f".  {job['match_summary']}"
            st.success(_msg)
        elif _status and _status != "not scored":
            st.warning(f"Discovered, not scored (status: {_status})")
        else:
            st.warning("Discovered — not scored")
        st.markdown("**Job description**")
        st.write(desc or "_(no description was captured for this job)_")
        # Adzuna's API returns only a short snippet, not the full posting, so the
        # text above can be much shorter than what the link shows. Flag it when the
        # captured description is snippet-sized. ATS-direct sources (Greenhouse /
        # Lever) and pasted custom URLs store the full description, so no note.
        if desc and len(desc) < 900:
            st.caption(
                "This is the short summary captured at discovery — aggregators like "
                "Adzuna only return a snippet. Open the full posting above for the "
                "complete description. (ATS-direct sources store the full text.)"
            )

    def _render_discovered() -> None:
        """Compact 'all discovered jobs' table (incl. not scored). Rendered for
        every run with discovered jobs — even when 0 were scored, which is exactly
        when surfacing the unscored set matters most."""
        _discovered = state.get("normalized_jobs") or []
        if not _discovered:
            return
        _rows = build_discovered_rows(_discovered, state.get("scored_jobs") or [])
        _unscored = sum(1 for r in _rows if not r["Status"].startswith("✅"))
        with st.expander(
            f"🔎 All discovered jobs ({len(_rows)}) — {_unscored} not scored",
            expanded=_unscored > 0,
        ):
            _funnel = discovery_funnel_summary(state.get("discovery_stats"))
            if _funnel:
                st.caption(_funnel)
            _dev = st.dataframe(
                pd.DataFrame(_rows),
                key=f"discovered_table_{wf_id}",
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "Posted": st.column_config.TextColumn("Posted", width="small"),
                    "Source": st.column_config.TextColumn(  # ADR-099
                        "Source", width="small",
                        help="Where this posting came from. Green = employer-direct "
                             "ATS (Greenhouse/Lever); yellow = aggregator "
                             "(Adzuna/Indeed/LinkedIn); 🔗 = your custom URL."),
                    "Status": st.column_config.TextColumn("Status", width="small"),
                },
            )
            _dsel = (_dev.selection.rows if _dev and getattr(_dev, "selection", None) else []) or []
            if _dsel and _dsel[0] < len(_discovered):
                _dj = dict(_discovered[_dsel[0]])
                _dj["_status"] = _rows[_dsel[0]]["Status"]
                if st.button("📄 View details", key=f"jd_disc_btn_{wf_id}"):
                    _show_job(_dj, _dj.get("job_description"))
            else:
                st.caption("Select a row to view its job details.")

    def _render_filtered_out() -> None:
        """ADR-079/094: jobs the relevance pre-filter (and the optional clearance
        filter) hard-dropped BEFORE scoring, with the per-job reason. The audit
        trail lives in discovery_stats.relevance_drops; this surfaces it so the
        user can see exactly which job was shed and why (the count alone is in the
        discovery funnel summary above)."""
        _rows = build_relevance_drop_rows(state.get("discovery_stats"))
        if not _rows:
            return
        with st.expander(
            f"🚦 Why {len(_rows)} job(s) were filtered out before scoring",
            expanded=False,
        ):
            st.caption(
                "The relevance pre-filter (a cheap reasoning pass, ADR-079) judged "
                "these a clear seniority or relevance mismatch for this profile and "
                "dropped them before paying to score them. If it looks too aggressive, "
                "loosen the experience window or turn it off under Settings -> search "
                "filters; it re-bands automatically per profile."
            )
            st.dataframe(
                pd.DataFrame(_rows),
                key=f"filtered_out_table_{wf_id}",
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Title": st.column_config.TextColumn("Title", width="medium"),
                    "Why dropped": st.column_config.TextColumn("Why dropped", width="small"),
                    "Reason": st.column_config.TextColumn("Reason", width="large"),
                    "Link": st.column_config.LinkColumn(
                        "Link", width="small", display_text="open"
                    ),
                },
            )

            # ADR-100: pull a dropped job back out of the discard bucket into the
            # "Maybe / Review later" list. No scoring here (Phase 1) - it just gets
            # set aside for the user to reconsider (and later score) from that list.
            _drops = (state.get("discovery_stats") or {}).get("relevance_drops") or []
            _uid = str(st.session_state.get("current_user_id", "0"))
            _saved_ids = {d.get("job_id") for d in _cached_review_later(_uid)}
            _movable = [d for d in _drops
                        if d.get("job_id") and d.get("job_id") not in _saved_ids]
            if _saved_ids:
                st.caption(
                    f"{len(_saved_ids)} of these are already on your Review later list."
                )
            if _movable:
                _label_to_id = {
                    (d.get("title") or d.get("job_id")): d.get("job_id") for d in _movable
                }
                _picked = st.multiselect(
                    "Move to Review later (reconsider these later, without scoring now)",
                    options=list(_label_to_id.keys()),
                    key=f"review_later_pick_{wf_id}",
                )
                if st.button("Move to Review later", key=f"review_later_btn_{wf_id}",
                             disabled=not _picked):
                    _moved = 0
                    for _lbl in _picked:
                        try:
                            api.add_review_later(_uid, wf_id, _label_to_id[_lbl])
                            _moved += 1
                        except api.ReviewLaterCapError:
                            st.warning("Your Review later list is full (cap reached).")
                            break
                        except Exception as exc:  # noqa: BLE001 - surface, never crash
                            st.warning(f"Could not move '{_lbl}': {exc}")
                    if _moved:
                        _cached_review_later.clear()
                        st.success(f"Moved {_moved} job(s) to your Review later list.")
                        st.rerun()

    # ── Manual scoring selection (ADR-060) ────────────────────────────────────
    # When a manual-selection run is parked after discovery, let the user pick
    # which discovered jobs are worth the research + scoring spend. Only the
    # selected jobs are scored; the rest are skipped.
    if status == "awaiting_scoring_selection":
        st.markdown("---")
        st.subheader("🧭 Select jobs to score")
        st.caption(
            "Manual selection is on for this run. Discovery cast a wide net; pick "
            "the jobs worth the research + scoring spend. Only the jobs you select "
            "are scored -- the rest are skipped, at no cost."
        )
        discovered = state.get("normalized_jobs") or []
        if not discovered:
            st.info("No jobs were discovered for this run.")
        else:
            # Review affordance: select a row to preview its full JD in a modal
            # before deciding which jobs are worth the scoring spend.
            _mrows = build_discovered_rows(discovered, [])
            _mev = st.dataframe(
                pd.DataFrame(_mrows),
                key=f"manual_review_table_{wf_id}",
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={"Posted": st.column_config.TextColumn("Posted", width="small")},
            )
            _msel = (_mev.selection.rows if _mev and getattr(_mev, "selection", None) else []) or []
            if _msel and _msel[0] < len(discovered):
                _mj = dict(discovered[_msel[0]])
                if st.button("📄 View details", key=f"jd_manual_btn_{wf_id}"):
                    _show_job(_mj, _mj.get("job_description"))
            else:
                st.caption("Select a row above to preview a job's details before picking.")

            options: dict[str, str] = {}
            for j in discovered:
                jid = j.get("id") or j.get("source_job_id") or ""
                if not jid:
                    continue
                options[jid] = (f"{j.get('title') or '(untitled)'} — "
                                f"{j.get('company') or '?'} ({j.get('location') or '?'})")
            picked = st.multiselect(
                f"{len(options)} discovered job(s)",
                options=list(options.keys()),
                format_func=lambda jid: options.get(jid, jid),
                key=f"score_select_{wf_id}",
            )
            b_sel, cap_sel = st.columns([1, 3])
            if b_sel.button("Score selected", type="primary",
                            disabled=not picked, key=f"score_btn_{wf_id}"):
                try:
                    resp = api.submit_scoring_selection(wf_id, picked)
                    st.success(
                        f"Scoring {resp.get('scoring_count', len(picked))} selected "
                        "job(s). Switch to Live Run Monitor to watch progress, or "
                        "reopen this run when it finishes."
                    )
                except Exception as exc:
                    st.error(f"Could not start scoring: {exc}")
            cap_sel.caption(
                f"{len(picked)} selected. Unselected jobs are skipped (not scored)."
            )
        st.stop()

    # ── Find & Score — Pipeline table (jobs × stages) ─────────────────────────
    st.markdown("---")
    st.subheader("📍 Find & Score — jobs surfaced and ranked")
    st.caption("What came back from the search and how each job scored across the three career tracks. "
               "Jobs whose best track score meets your threshold automatically advance to deep review.")

    jobs_df = _cached_workflow_jobs(wf_id)
    if jobs_df.empty:
        st.info("No jobs were scored for this run — the discovered set is below.")
        _render_discovered()
    else:
        # ADR-057: hide-by-default toggle for excluded rows in this run.
        n_excluded = int(jobs_df["excluded"].fillna(0).sum()) if "excluded" in jobs_df.columns else 0
        show_excluded = False
        if n_excluded:
            show_excluded = st.toggle(
                f"Show {n_excluded} excluded job(s) in this run",
                value=False,
                key=f"show_excluded_{wf_id}",
            )
        if not show_excluded and "excluded" in jobs_df.columns:
            jobs_df = jobs_df[(jobs_df["excluded"].fillna(0) == 0)].reset_index(drop=True)

        view_df = jobs_df.copy()
        view_df["🚫"] = view_df["excluded"].fillna(0).apply(lambda v: "🚫" if v else "")
        # ADR-080: posting freshness as a leading column.
        _posted = view_df["posted_at"] if "posted_at" in view_df.columns else None
        view_df["Posted"] = _posted.apply(format_posting_age_short) if _posted is not None else ""
        # Trim horizontal scroll: collapse the 3 pipeline checkmarks into one
        # compact R/A/P cell (Reviewed / Advised / Prep) instead of 3 columns.
        view_df["R/A/P"] = view_df.apply(
            lambda r: _checked(r["reviewed_at"]) + _checked(r["advised_at"]) + _checked(r["prep_at"]),
            axis=1,
        )

        # ADR-071: show only the track columns active for this run's profile.
        # The active set is read from the run's stored effective_config.
        _track_short = {"ic": "Tech", "architect": "Arch", "management": "Lead"}
        active_tracks = get_active_tracks(state)
        active_score_cols = [TRACK_TO_SCORE_KEY[t] for t in active_tracks]
        active_short = {TRACK_TO_SCORE_KEY[t]: _track_short[t] for t in active_tracks}

        # Posted is 2nd (after Title); Found dropped (Scored timestamp is enough);
        # the 3 stage flags are now one R/A/P cell — all to reduce the scroll.
        display_cols = (
            ["🚫", "title", "Posted", "company", "location", "url", "overall_score"]
            + active_score_cols
            + ["R/A/P", "scored_at"]
        )
        rename_map = {
            "title": "Title", "company": "Company", "location": "Location", "url": "URL",
            "overall_score": "Overall", "scored_at": "Scored",
            **{col: active_short[col] for col in active_score_cols},
        }
        col_config = {
            "🚫":      st.column_config.TextColumn("🚫", width="small",
                                                   help="🚫 = excluded from cross-run analytics + future discovery"),
            "Posted":  st.column_config.TextColumn("Posted", width="small",
                                                   help="Age of the posting (ADR-080). Blank = source gave no date."),
            "URL":     st.column_config.LinkColumn("URL", width="small"),
            "R/A/P":   st.column_config.TextColumn("R/A/P", width="small",
                                                   help="Reviewed / Advised / Prep — pipeline stages reached"),
            "Overall": st.column_config.ProgressColumn("Overall", min_value=0, max_value=100, format="%d"),
            **{
                active_short[col]: st.column_config.ProgressColumn(
                    active_short[col], min_value=0, max_value=100, format="%d"
                )
                for col in active_score_cols
            },
        }
        ev = st.dataframe(
            view_df[display_cols].rename(columns=rename_map),
            key=f"jobs_table_{wf_id}",
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config=col_config,
        )

        # ADR-057: per-row exclude / un-exclude action. Single-row selection
        # is the same affordance Workflow History uses, kept consistent.
        sel_rows = (ev.selection.rows if ev and getattr(ev, "selection", None) else []) or []
        sel_job: dict | None = None
        if sel_rows and sel_rows[0] < len(jobs_df):
            sel_job = jobs_df.iloc[sel_rows[0]].to_dict()

        # Selected-job actions — one consistent cluster driven by the row click:
        # View details (quick modal), Open (the Opportunity page), Exclude. No
        # separate job picker; the table IS the picker.
        b_view, b_drill, b_excl, b_cap = st.columns([1, 1, 1, 2])
        if sel_job:
            is_excluded = bool(sel_job.get("excluded") or 0)
            if b_view.button("📄 View details", key=f"jd_btn_{wf_id}", use_container_width=True):
                _show_job(sel_job, _desc_by_id.get(sel_job.get("job_id")))
            if b_drill.button("Open ▶", key=f"drill_in_job_{wf_id}", use_container_width=True):
                _navigate("Opportunity", detail_workflow_id=wf_id, detail_job_id=sel_job["job_id"])
            excl_label = "♻ Un-exclude" if is_excluded else "🚫 Exclude"
            if b_excl.button(excl_label, key=f"excl_btn_{wf_id}", use_container_width=True):
                try:
                    if is_excluded:
                        api.unexclude_job(sel_job["job_id"])
                        st.success(f"Un-excluded: {sel_job.get('title', '')}")
                    else:
                        api.exclude_job(sel_job["job_id"])
                        st.success(f"Excluded: {sel_job.get('title', '')}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Action failed: {exc}")
            b_cap.caption(
                f"Selected: **{sel_job.get('title','(untitled)')}** @ {sel_job.get('company','')}"
                + ("  ·  currently excluded" if is_excluded else "")
            )
        else:
            b_view.button("📄 View details", disabled=True, use_container_width=True,
                          key=f"jd_btn_disabled_{wf_id}")
            b_drill.button("Open ▶", disabled=True, use_container_width=True,
                           key=f"drill_disabled_{wf_id}")
            b_excl.button("🚫 Exclude", disabled=True, use_container_width=True,
                          key=f"excl_btn_disabled_{wf_id}")
            b_cap.caption("Select a job row above to view details, open it, or exclude.")

        st.caption("Tip: click a job row above, then use **View details** (quick look) "
                   "or **Open** (the Opportunity page: fit, gaps, deep review, tailor, prep).")

        # Discovered jobs (incl. not scored) — surfaced for every run below.
        _render_discovered()

    # The per-job Review / interview Prep / Tailoring action region moved to the
    # Opportunity page (ADR-088 Tier 2). Open a job from the table above to reach
    # its fit, gaps, deep review, tailoring drafts, and interview prep.

    # Jobs the relevance pre-filter dropped before scoring (with the per-job
    # reason). Rendered for both the scored and the nothing-scored paths.
    _render_filtered_out()

    # ── Diagnostics — collapsed by default to keep the action surfaces above ──
    st.markdown("---")
    st.subheader("🔧 Diagnostics")
    st.caption("Settings used, cost breakdown, limits hit, and any errors. "
               "Collapsed by default — open if a run looks off.")

    cfg_used = state.get("effective_config") or {}
    sc = state.get("search_criteria") or {}
    cc = state.get("custom_urls") or []
    breakdown = _cached_cost_breakdown(wf_id)
    findings = analyze(state)
    errors = state.get("errors") or []

    with st.expander("Settings used for this run", expanded=False):
        if cfg_used or sc or cc:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Search criteria**")
                st.json(sc, expanded=False)
                if cc:
                    st.markdown(f"**Custom URLs** ({len(cc)})")
                    for u in cc:
                        st.markdown(f"- {u}")
            with col_b:
                st.markdown("**Effective config**")
                st.json(cfg_used, expanded=False)
        else:
            st.caption("No settings snapshot stored for this run (likely a pre-snapshot legacy run).")

    # ADR-074 Gap 3: per-run rollup incl. wall-clock duration, available for any
    # run (in-graph runs have a run_metrics row; out-of-graph runs are computed
    # lazily from llm_calls). `computed` marks the derived path.
    rollup = _cached_run_metrics(wf_id)
    if rollup["calls"] or rollup["duration_ms"]:
        src = "computed from llm_calls" if rollup["computed"] else "from run_metrics"
        st.caption(
            f"Run rollup ({src}): {rollup['calls']} LLM call(s) · "
            f"${rollup['cost_usd']:.4f} · {rollup['duration_ms'] / 1000:.1f}s wall-clock"
        )

    if breakdown["rows"]:
        agg = breakdown["aggregate"]
        # Promoted out of a collapsed expander to its own visible subsection.
        # Cost is a primary operational concern; users shouldn't have to dig.
        st.markdown(f"#### 💰 Cost breakdown — ${agg['cost_usd']:.4f} across {agg['calls']} calls")
        st.caption(
            f"{agg['calls']} calls · {agg['tokens_input']:,} tokens in · "
            f"{agg['tokens_output']:,} tokens out · ~{int(agg['avg_latency_ms'])} ms avg latency. "
            "See the **System Dashboard** view for cross-run trends."
        )
        cost_df = pd.DataFrame(breakdown["rows"])
        # Side-by-side: bar chart and numeric table, so both visual and exact reads work.
        c_chart, c_table = st.columns([2, 3])
        with c_chart:
            chart_df = cost_df.sort_values("cost_usd", ascending=True)
            fig = px.bar(
                chart_df, x="cost_usd", y="agent_name", orientation="h",
                text="cost_usd",
                labels={"cost_usd": "Cost ($)", "agent_name": "Agent"},
                color="cost_usd", color_continuous_scale="oranges",
            )
            fig.update_traces(texttemplate="$%{x:.4f}", textposition="outside")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                              height=max(180, 32 * len(chart_df)),
                              coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        with c_table:
            tbl = cost_df.rename(columns={
                "agent_name": "Agent", "provider": "Provider", "model": "Model",
                "calls": "Calls", "tokens_input": "Tokens in",
                "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
                "avg_latency_ms": "Avg latency",
            })
            st.dataframe(
                tbl, hide_index=True, use_container_width=True,
                column_config={
                    "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                    "Avg latency": st.column_config.NumberColumn(format="%d ms"),
                },
            )

    # Step timeline (ADR-074 step_executions): per-node started/completed timestamps
    # + duration for THIS run, the data the Live monitor shows live. Surfaced here so a
    # finished run's per-step timing is inspectable from its detail page, not only the
    # live feed. Read-only; from GET /workflows/{id}/steps.
    steps_df = _cached_step_executions(wf_id)
    with st.expander(
        "Step timeline" + (f" — {len(steps_df)} step(s)" if not steps_df.empty else ""),
        expanded=False,
    ):
        if steps_df.empty:
            st.caption("No step executions recorded for this run "
                       "(likely a pre-instrumentation legacy run).")
        else:
            _STATUS_ICON = {"completed": "✅", "failed": "❌", "started": "🔄"}
            tl = steps_df.copy()
            tl[""] = tl["status"].map(_STATUS_ICON).fillna("⚪")
            # Show wall-clock times (HH:MM:SS); the full date is in started_at if needed.
            tl["Started"] = tl["started_at"].astype(str).str.slice(11, 19)
            tl["Completed"] = (tl["completed_at"].astype(str).str.slice(11, 19)
                               if "completed_at" in tl else "")
            tl["Duration"] = tl["duration_ms"].apply(
                lambda x: f"{int(x):,} ms" if pd.notna(x) and x else "—"
            )
            total_ms = tl["duration_ms"].dropna().sum() if "duration_ms" in tl else 0
            st.caption(f"{len(tl)} steps · {total_ms / 1000:.1f}s total node time "
                       "(node boundaries; excludes between-node overhead).")
            st.dataframe(
                tl[["", "step", "status", "Started", "Completed", "Duration"]]
                .rename(columns={"step": "Step", "status": "Status"}),
                hide_index=True, use_container_width=True,
            )

    # Limits & Constraints — keep open when something fired so the user notices
    _has_findings = bool(findings)
    with st.expander(
        "Limits & Constraints" + (f" — {len(findings)} finding(s)" if _has_findings else ""),
        expanded=_has_findings,
    ):
        if not findings:
            st.success("No execution limits clipped this run.")
        else:
            for f in findings:
                (st.warning if f["severity"] == "warning" else st.info)(f["message"])

    # Errors — open when present
    if errors:
        with st.expander(f"Errors ({len(errors)})", expanded=True):
            for err in errors:
                st.json(err, expanded=False)
