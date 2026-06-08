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
from app.ui.views import (
    history,
    live_monitor,
    matches,
    opportunity,
    profiles,
    resume_clinic,
    run_report,
    settings,
    start_run,
    system_dashboard,
    workflow_detail,
)

# name -> render(ctx) callable. Every nav view is now registered (Phase 4 complete).
REGISTRY: dict[str, Callable[[ViewContext], None]] = {
    "Workflow History": history.render,
    "Workflow Detail": workflow_detail.render,
    "System Dashboard": system_dashboard.render,
    "Profiles": profiles.render,
    "Settings": settings.render,
    "Resume Clinic": resume_clinic.render,
    "Run Report": run_report.render,
    "Matches": matches.render,
    "Live Run Monitor": live_monitor.render,
    "Opportunity": opportunity.render,
    "Start New Run": start_run.render,
}
