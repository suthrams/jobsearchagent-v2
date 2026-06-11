"""Sidebar navigation model + programmatic view dispatch for the Streamlit UI.

ADR-088 (journey reorg, Phase 0): the UI uses Streamlit **native multipage**
(``st.navigation`` / ``st.Page``). This module is the single source of truth for:

  * ``NAV_GROUPS``       - ordered journey groups -> the views shown in the sidebar
  * ``DESTINATION_VIEWS``- views reachable only by a click (hidden from the sidebar)
  * ``DISPLAY_TITLE``    - internal view name -> the user-facing title
  * ``DEFAULT_VIEW``     - the landing page (Matches, the payoff)
  * ``ViewContext``      - the sidebar filter inputs handed to every ``render(ctx)``
  * ``_navigate``        - programmatic page switch (now via ``st.switch_page``)

Internal view names (e.g. ``"Workflow History"``) are invisible route identifiers.
The user only ever sees ``DISPLAY_TITLE`` (e.g. ``"Searches"``). Keeping the
internal names stable means the ADR-088 rename touches only ``DISPLAY_TITLE`` -
not the ~12 ``_navigate`` call sites, the ``views/REGISTRY`` keys, or the per-view
detail-target logic. The "stop saying Workflow in the chrome" goal is fully met
because the chrome renders titles, never the internal names.

The entrypoint builds the ``st.Page`` objects (one per view, wrapping
``REGISTRY[name](ctx)``) and calls ``register_pages`` so ``_navigate`` can switch
to a view by name. This module deliberately calls no ``st.*`` at import time, so
it stays importable in tests without a Streamlit runtime.
"""
from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

# ── Journey groups (the sidebar) ──────────────────────────────────────────────
# The system group's section header. ADR-088 originally used an unlabeled rule glyph
# here ("a rule, no operator noun"), but the owner asked for an explicit label so the
# Settings + Spend & Health screens read as a clear system-wide group rather than a
# faint divider (revised 2026-06-08). Kept terse + uppercase to match FIND / RESUME /
# MY OPPORTUNITIES; "SYSTEM" umbrellas both config (Settings) and monitoring
# (Spend & Health) without operator jargon.
OPERATOR_SECTION = "SYSTEM"

# Ordered mapping of sidebar SECTION HEADER -> the internal view names under it.
# Dict order is render order, so the operator section (the rule) sits last.
NAV_GROUPS: dict[str, list[str]] = {
    "FIND": ["Start New Run", "Workflow History"],
    "MY OPPORTUNITIES": ["Matches", "Review Later"],
    "RESUME": ["Resume Clinic", "Profiles"],
    OPERATOR_SECTION: ["Settings", "System Dashboard"],
}

# Views reached only by clicking a row/button (each renders an in-app Back to its
# origin). Registered with st.navigation as ``visibility="hidden"`` so they route
# but never appear in the sidebar (ADR-088 section F).
DESTINATION_VIEWS: list[str] = [
    "Workflow Detail",
    "Opportunity",
    "Live Run Monitor",
    "Run Report",
]

# The user-facing title for each internal view name (the ADR-088 journey rename).
# Every NAV_VIEW must have an entry (asserted by test_ui_structure).
DISPLAY_TITLE: dict[str, str] = {
    # FIND
    "Start New Run": "New search",
    "Workflow History": "Searches",
    # MY OPPORTUNITIES
    "Matches": "Matches",
    "Review Later": "Maybe / Review later",
    # RESUME
    "Resume Clinic": "Resume Clinic",
    "Profiles": "Profiles",
    # operator group (below the rule)
    "Settings": "Settings",
    "System Dashboard": "System Dashboard",
    # destinations (hidden from the sidebar)
    "Workflow Detail": "Search detail",
    "Opportunity": "Opportunity",
    "Live Run Monitor": "Live monitor",
    "Run Report": "Run report",
}

# The default landing page (ADR-088 D: land on Matches, the payoff).
DEFAULT_VIEW: str = "Matches"

# All real view names: the grouped sidebar views (in group order) + the hidden
# destinations. This is the set the REGISTRY must cover (test_ui_structure).
NAV_VIEWS: list[str] = [
    name for group in NAV_GROUPS.values() for name in group
] + list(DESTINATION_VIEWS)


@dataclass(frozen=True)
class ViewContext:
    """The sidebar-derived inputs a view needs, passed to every ``render(ctx)``.

    The entrypoint builds one of these from the sidebar filter widgets and hands
    it to the dispatched view. Widget-independent inputs (the active workflow id,
    the current user id) stay on ``st.session_state`` and are read there; only the
    cross-run filter widgets travel through here.
    """
    min_score: int
    search: str
    include_excluded: bool


# ── Programmatic navigation ───────────────────────────────────────────────────
# The entrypoint records the StreamlitPage objects here each run (before pg.run()),
# so _navigate can switch to a view by its internal name. Empty in tests (no
# runtime) - _navigate is only ever called from inside a rendering view.
_PAGE_REGISTRY: dict[str, object] = {}


def register_pages(pages: dict[str, object]) -> None:
    """Entrypoint hook: record ``{internal view name: st.Page}`` so ``_navigate``
    can switch programmatically. Called once per run, before ``pg.run()``."""
    _PAGE_REGISTRY.clear()
    _PAGE_REGISTRY.update(pages)


def _navigate(view_name: str, **state_updates) -> None:
    """Switch to a view by its internal name, carrying any companion session state.

    Under native multipage this is a real ``st.switch_page`` (which reruns); the
    old ``_pending_nav`` radio workaround is gone. ``state_updates`` set the
    destination's inputs (e.g. ``detail_workflow_id``) before the switch.
    """
    for k, v in state_updates.items():
        st.session_state[k] = v
    # When navigation explicitly changes the detail target, force the Detail view's
    # text_input to re-sync; without this a user-typed value would survive the next
    # row click and override the new target.
    if "detail_workflow_id" in state_updates:
        st.session_state.pop("_detail_wf_synced", None)
    page = _PAGE_REGISTRY.get(view_name)
    if page is None:
        # Should not happen in normal operation (the registry is built before any
        # view renders). Surface loudly rather than silently no-op.
        st.error(f"Cannot navigate to {view_name!r}: no page registered.")
        return
    st.switch_page(page)


def back_button(target_view: str, *, label: str | None = None, **state_updates) -> bool:
    """Render an in-app Back control that returns to ``target_view``.

    Click-through destinations have no sidebar entry, and under native multipage the
    browser Back button misleads (ADR-088 F / UX-review R-1), so every destination
    needs an explicit in-app Back. The label defaults to the destination's
    user-facing title ("Back to Searches", etc.), so it tracks ``DISPLAY_TITLE``
    automatically. Returns True if it was clicked (it navigates before returning).
    """
    text = label or f"← Back to {DISPLAY_TITLE.get(target_view, target_view)}"
    if st.button(text, key=f"_back_{target_view.replace(' ', '_')}"):
        _navigate(target_view, **state_updates)
        return True
    return False
