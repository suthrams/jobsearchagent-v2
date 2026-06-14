"""Shared renderer for the Research Agent's per-job output (ADR-105).

The Research Agent (bounded ReAct, before scoring) gathers company/role signals that
shape the score, but its output was discarded until ADR-105 persisted it on `job_scores`.
This panel surfaces it on both the per-job Opportunity page and the run-level Search
detail page, so the user can finally see what the research agent found.

Pure render helper: takes the parsed `ResearchContext` dict (model_dump shape) and is
tolerant of missing/empty fields (Haiku omits the signal lists on simple queries).
"""
from __future__ import annotations

import streamlit as st


def _signal_line(label: str, items) -> None:
    items = [str(x) for x in (items or []) if str(x).strip()]
    if items:
        st.markdown(f"**{label}:** " + " · ".join(items))


def render_research(research: dict | None, *, key: str = "") -> None:
    """Render the research findings. `research` is the parsed ResearchContext dict
    (the `data` blob from the pipeline / research read), or None/empty when a run
    predates ADR-105 or research found nothing."""
    data = research or {}
    if not data or not (data.get("company_summary") or data.get("role_context")):
        st.caption("No research findings recorded for this job.")
        return

    if data.get("company_summary"):
        st.markdown(f"**Company:** {data['company_summary']}")
    if data.get("role_context"):
        st.markdown(f"**Role context:** {data['role_context']}")

    _signal_line("Technology signals", data.get("technology_signals"))
    _signal_line("Leadership signals", data.get("leadership_signals"))
    _signal_line("Domain signals", data.get("domain_signals"))

    risks = [str(x) for x in (data.get("risk_flags") or []) if str(x).strip()]
    if risks:
        st.warning("**Risk flags:** " + " · ".join(risks))

    steps = data.get("research_steps") or []
    if steps:
        with st.expander(f"How it researched ({len(steps)} step(s))", expanded=False):
            for s in steps:
                n = s.get("step_number", "?")
                tool = s.get("tool_used", "?")
                obs = s.get("observation_summary", "")
                st.markdown(f"**{n}. `{tool}`** — {obs}")

    conf = data.get("confidence")
    if conf is not None:
        st.caption(f"Research confidence: {conf}/100")
