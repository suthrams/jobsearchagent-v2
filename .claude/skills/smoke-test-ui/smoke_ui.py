"""Headless UI smoke test - drives every Streamlit view through the real dispatch.

Loads app/ui/streamlit_app.py via Streamlit's AppTest harness and renders each
view in app.ui.nav.NAV_VIEWS through the REGISTRY dispatch, asserting none raises
an unhandled exception. This is the fast regression check for the UI refactor
(docs/architecture/ui_refactor_plan.md): it executes each view's render(ctx) body,
so a missing import, broken dispatch, or signature drift surfaces immediately.

Run from the project root:

    # Optional but recommended - start the backend first so config/users/providers
    # reads return real data (the harness still passes without it; api calls are
    # wrapped in try/except and degrade to the offline fallback):
    python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 &

    python .claude/skills/smoke-test-ui/smoke_ui.py

Exit code 0 = all views rendered clean; 1 = at least one raised.

The two detail screens (Workflow Detail, Job Detail) are seeded with a real
workflow_id / job_id pulled from data/v2.db so their data-heavy paths render
instead of the empty inline picker. If the DB has no runs, they fall back to the
picker path (still a valid render).
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


def _run_view(view, extra):
    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state["current_user_id"] = "0"
    at.session_state["sidebar_view"] = view
    for k, v in extra.get(view, {}).items():
        at.session_state[k] = v
    at.run()
    return list(at.exception)


def main() -> int:
    wf_id, job_id = _sample_ids()
    extra = {
        "Workflow Detail": {"detail_workflow_id": wf_id, "detail_job_id": None},
        "Job Detail": {"detail_workflow_id": wf_id, "detail_job_id": job_id},
    }

    results = []
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
        except Exception as e:
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
