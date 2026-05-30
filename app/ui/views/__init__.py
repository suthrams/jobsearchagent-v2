"""Per-screen Streamlit views for the v2 UI + the dispatch registry.

Phase 0 of the UI refactor (docs/architecture/ui_refactor_plan.md) created this
package; Phase 3+ fills it. Each screen is a module exposing ``render(ctx)`` that
performs all of that view's ``st.*`` calls. Importing a view module must NOT
execute any Streamlit call (the body lives inside ``render()``), so the structure
tests can import every view without a Streamlit runtime.

``REGISTRY`` maps each migrated view's nav name to its render callable. The
entrypoint dispatches through it and falls back to its legacy ``if/elif`` chain for
views not yet migrated, so the registry is a SUBSET of ``nav.NAV_VIEWS`` during
migration. The registry lives here (not in nav.py) so view modules can import
``ViewContext`` from the leaf ``nav`` module without an import cycle.
"""
from __future__ import annotations

from typing import Callable

from app.ui.nav import ViewContext
from app.ui.views import analytics, job_detail, live_monitor, run_report, start_run

# name -> render(ctx) callable. Grows one entry per migrated view (Phases 3-4).
REGISTRY: dict[str, Callable[[ViewContext], None]] = {
    "Run Report": run_report.render,
    "Top Matches": analytics.render_top_matches,
    "IC Track": analytics.render_ic_track,
    "Architect Track": analytics.render_architect_track,
    "Management Track": analytics.render_management_track,
    "Companies": analytics.render_companies,
    "Live Run Monitor": live_monitor.render,
    "Job Detail": job_detail.render,
    "Start New Run": start_run.render,
}
