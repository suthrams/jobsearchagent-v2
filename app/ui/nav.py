"""Sidebar navigation set + view dispatch registry for the Streamlit UI.

Phase 0 of the UI refactor (docs/architecture/ui_refactor_plan.md). This module is
the single source of truth for the set of views and the dispatch registry. It
deliberately does NOT import streamlit, so it stays importable in tests without a
Streamlit runtime.

Migration state: views are dispatched by the legacy ``if/elif`` chain in
streamlit_app.py and cut over to the registry (``app/ui/views/__init__.py::REGISTRY``)
one at a time as they are extracted into ``app/ui/views/`` (Phases 3-4). The
registry is therefore a SUBSET of ``NAV_VIEWS`` during migration; the entrypoint
dispatches through it when a view is present and falls back to its inline block
otherwise. When migration completes, ``set(REGISTRY) == set(NAV_VIEWS)``.

The registry lives in ``views/__init__.py`` (not here) so that view modules can
import ``ViewContext`` from this leaf module without a cycle (views -> nav, never
nav -> views).
"""
from __future__ import annotations

from dataclasses import dataclass

# The non-selectable group header shown in the sidebar radio. Selecting it shows a
# hint and stops; it is not a view. (Box-drawing glyphs are fine here - this string
# is rendered in the browser, and Streamlit UI files may use non-ASCII.)
SEPARATOR = "─── Cross-Run Analytics ───"

# Ordered sidebar radio entries, including the separator at its display position.
# This is the exact list the entrypoint passes to st.radio.
NAV_ITEMS: list[str] = [
    "Workflow History",
    "Workflow Detail",
    "Job Detail",
    "Start New Run",
    "Live Run Monitor",
    "Run Report",
    "Resume Clinic",
    "Settings",
    "Profiles",
    SEPARATOR,
    "Cost Dashboard",
    "Top Matches",
    "IC Track",
    "Architect Track",
    "Management Track",
    "Companies",
]

# The real, selectable view names (NAV_ITEMS minus the separator).
NAV_VIEWS: list[str] = [item for item in NAV_ITEMS if item != SEPARATOR]


@dataclass(frozen=True)
class ViewContext:
    """The sidebar-derived inputs a view needs, passed to every ``render(ctx)``.

    The entrypoint builds one of these after the sidebar widgets are read and
    hands it to the dispatched view. Widget-independent inputs (the active
    workflow id, the current user id) stay on ``st.session_state`` and are read
    there; only the cross-run filter widgets travel through here.
    """
    min_score: int
    search: str
    include_excluded: bool
