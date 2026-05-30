"""Structural invariants for the Streamlit UI refactor (docs/architecture/ui_refactor_plan.md).

Phase 0 harness. These are forcing functions (cf. the model-pin and PII-redaction
invariants): they fail the build if the view registry drifts from the nav set, if
a view module stops exposing a callable render(), or if importing a view module
executes Streamlit at import time.

They deliberately do NOT import streamlit_app.py: the entrypoint runs st.* at
module scope and only works inside a Streamlit runtime. The refactor's whole point
is that everything OTHER than the entrypoint is importable without that runtime.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from app.ui import nav

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "app" / "ui" / "streamlit_app.py"


def test_refactor_packages_import_clean():
    """The refactor modules import without a Streamlit runtime (no st.* runs at
    import: formatting is pure; data only applies @st.cache_data decorators)."""
    importlib.import_module("app.ui.nav")
    importlib.import_module("app.ui.views")
    importlib.import_module("app.ui.components")
    importlib.import_module("app.ui.formatting")
    importlib.import_module("app.ui.data")


def test_nav_views_are_unique_and_exclude_separator():
    assert nav.SEPARATOR in nav.NAV_ITEMS
    assert nav.SEPARATOR not in nav.NAV_VIEWS
    assert len(nav.NAV_VIEWS) == len(set(nav.NAV_VIEWS)), "duplicate view names"
    assert nav.NAV_VIEWS == [i for i in nav.NAV_ITEMS if i != nav.SEPARATOR]


def test_view_registry_is_a_subset_of_nav_and_all_callable():
    """During migration VIEW_REGISTRY grows from {} until it covers every view. It
    must never name something that is not a real view, and every entry must be a
    callable render(). When migration completes, set(VIEW_REGISTRY) == NAV_VIEWS."""
    assert set(nav.VIEW_REGISTRY) <= set(nav.NAV_VIEWS), (
        "VIEW_REGISTRY has a key that is not a nav view"
    )
    for name, fn in nav.VIEW_REGISTRY.items():
        assert callable(fn), f"VIEW_REGISTRY[{name!r}] is not callable"


def test_registered_views_expose_render_without_running_streamlit():
    """Every migrated view module must expose a callable render(); importing it
    must not execute Streamlit (the body lives inside render()). Trivially passes
    while VIEW_REGISTRY is empty (Phase 0) and tightens as views migrate."""
    for name in nav.VIEW_REGISTRY:
        fn = nav.VIEW_REGISTRY[name]
        assert callable(fn), f"{name!r} render is not callable"


def test_entrypoint_sources_its_radio_from_nav():
    """Forcing function: the sidebar radio must source its options from
    nav.NAV_ITEMS, not a re-hardcoded list, so nav.py stays the single source of
    truth. Source-scan, because the entrypoint cannot be imported (runs st.* at
    import)."""
    src = _ENTRYPOINT.read_text(encoding="utf-8")
    assert "nav.NAV_ITEMS" in src, "entrypoint should pass nav.NAV_ITEMS to st.radio"
    assert "nav.SEPARATOR" in src, "entrypoint should compare against nav.SEPARATOR"
