"""Headless UI smoke test - renders the shell + every Streamlit view.

ADR-088 moved the UI to Streamlit native multipage (st.navigation / st.Page), so a
single AppTest.from_file run only executes the *default* page (Matches). Hidden
destination pages (Search detail, Job detail, Live monitor, Run report) have no
sidebar entry to click. So this harness has two passes:

  1. Shell pass: run app/ui/streamlit_app.py once via AppTest.from_file. This
     exercises the entrypoint - session state, st.navigation wiring, the shared
     sidebar (profile selector + filters + Active Run), and the default landing
     (Matches). Asserts it renders with no unhandled exception.

  2. Per-view pass: render every view in app.ui.nav.NAV_VIEWS in isolation through
     a from_function harness that mirrors the entrypoint's page factory - build a
     ViewContext and dispatch through REGISTRY[name](ctx). This executes each
     view's render(ctx) body (including the hidden destinations), so a missing
     import, broken dispatch, or signature drift surfaces immediately.

Run from the project root:

    # Optional but recommended - start the backend first so config/users/providers
    # reads return real data (the harness still passes without it; api calls are
    # wrapped in try/except and degrade to the offline fallback):
    python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 &

    python .claude/skills/smoke-test-ui/smoke_ui.py

Exit code 0 = shell + all views rendered clean; 1 = at least one raised.

The detail screens (Workflow Detail / Opportunity) are seeded with a real
workflow_id / job_id pulled from data/v2.db so their data-heavy paths render
instead of the empty picker. If the DB has no runs, they fall back to the picker
path (still a valid render).
"""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

from app.ui import nav  # noqa: E402

APP = os.path.join(ROOT, "app", "ui", "streamlit_app.py")
DB = os.path.join(ROOT, "data", "v2.db")


def _sample_ids():
    """A real (workflow_id, job_id) from the DB so detail screens render content."""
    if not os.path.exists(DB):
        return None, None
    try:
        c = sqlite3.connect(DB)
        wf = c.execute(
            "SELECT id FROM workflow_runs "
            "WHERE status IN ('completed','completed_with_errors') "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        wf_id = wf[0] if wf else None
        job_id = None
        if wf_id:
            job = c.execute(
                "SELECT job_id FROM job_scores WHERE workflow_run_id=? LIMIT 1",
                (wf_id,),
            ).fetchone()
            job_id = job[0] if job else None
        c.close()
        return wf_id, job_id
    except Exception:
        return None, None


def _view_harness() -> None:
    """Script body for the per-view pass: render one view's render(ctx) in isolation,
    mirroring the entrypoint's page factory. Inputs arrive on session_state."""
    import streamlit as st  # noqa: PLC0415

    from app.ui import nav as _nav  # noqa: PLC0415
    from app.ui.views import REGISTRY  # noqa: PLC0415

    # Mirror the entrypoint's session-state defaults so views that read them render
    # (the isolated harness skips the entrypoint init block that normally sets these).
    for _k, _default in (
        ("workflow_id", None),
        ("last_status", None),
        ("last_response", None),
        ("detail_workflow_id", None),
        ("detail_job_id", None),
        ("config_cache", None),
        ("current_user_id", "0"),
        ("onboard_step", 1),
        ("onboard_new_user_id", None),
    ):
        if _k not in st.session_state:
            st.session_state[_k] = _default

    name = st.session_state["_smoke_view"]
    ctx = _nav.ViewContext(
        min_score=int(st.session_state.get("flt_min_score", 75)),
        search=str(st.session_state.get("flt_search", "") or ""),
        include_excluded=bool(st.session_state.get("flt_include_excluded", False)),
    )
    REGISTRY[name](ctx)


def _run_shell() -> tuple[str, str]:
    """Pass 1: run the entrypoint (default landing). Returns (status, detail)."""
    try:
        at = AppTest.from_file(APP, default_timeout=90)
        at.session_state["current_user_id"] = "0"
        at.run()
        excs = list(at.exception)
        if excs:
            msgs = [str(getattr(e, "value", None) or getattr(e, "message", None)
                        or e)[:300] for e in excs]
            return "FAIL", " | ".join(msgs)
        return "PASS", ""
    except Exception as e:  # noqa: BLE001
        return "ERROR", f"{type(e).__name__}: {e}"[:300]


def _run_view(view, extra) -> list:
    at = AppTest.from_function(_view_harness, default_timeout=90)
    at.session_state["current_user_id"] = "0"
    at.session_state["_smoke_view"] = view
    for k, v in extra.get(view, {}).items():
        at.session_state[k] = v
    at.run()
    return list(at.exception)


def main() -> int:
    wf_id, job_id = _sample_ids()
    extra = {
        "Workflow Detail": {"detail_workflow_id": wf_id, "detail_job_id": None},
        "Opportunity": {"detail_workflow_id": wf_id, "detail_job_id": job_id},
    }

    results = []

    shell_status, shell_detail = _run_shell()
    results.append(("(entrypoint shell)", shell_status, shell_detail))

    for view in nav.NAV_VIEWS:
        try:
            excs = _run_view(view, extra)
            if excs:
                msgs = []
                for e in excs:
                    m = getattr(e, "value", None) or getattr(e, "message", None) or str(e)
                    msgs.append(str(m)[:300])
                results.append((view, "FAIL", " | ".join(msgs)))
            else:
                results.append((view, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((view, "ERROR", f"{type(e).__name__}: {e}"[:300]))

    npass = sum(1 for _, s, _ in results if s == "PASS")
    print("\n==== UI SMOKE TEST (AppTest, headless) ====")
    if not wf_id:
        print("note: no completed run in data/v2.db; detail screens used the picker path.")
    for view, status, detail in results:
        mark = "OK " if status == "PASS" else "XX "
        print(f"{mark} {view:<22} {status}")
        if detail:
            print(f"      -> {detail}")
    print(f"\n{npass}/{len(results)} screens rendered with no unhandled exception.")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
