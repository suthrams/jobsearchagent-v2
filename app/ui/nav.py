"""Sidebar navigation set + view dispatch registry for the Streamlit UI.

Phase 0 of the UI refactor (docs/architecture/ui_refactor_plan.md). This module is
the single source of truth for the set of views and the dispatch registry. It
deliberately does NOT import streamlit, so it stays importable in tests without a
Streamlit runtime.

Migration state: views are dispatched by the legacy ``if/elif`` chain in
streamlit_app.py and cut over to ``VIEW_REGISTRY`` one at a time as they are
extracted into ``app/ui/views/`` (Phases 3-4). ``VIEW_REGISTRY`` is therefore a
SUBSET of ``NAV_VIEWS`` during migration; the entrypoint dispatches through the
registry when a view is present and falls back to its inline block otherwise. When
migration completes, ``set(VIEW_REGISTRY) == set(NAV_VIEWS)``.
"""
from __future__ import annotations

from typing import Callable

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

# name -> render() callable. Populated as views migrate into app/ui/views/.
# Empty in Phase 0; the entrypoint falls back to its legacy if/elif chain for any
# view not yet registered here.
VIEW_REGISTRY: dict[str, Callable[[], None]] = {}
