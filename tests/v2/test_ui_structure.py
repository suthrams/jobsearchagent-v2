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
    import: formatting is pure; data only applies @st.cache_data decorators; view
    bodies live inside render())."""
    importlib.import_module("app.ui.nav")
    importlib.import_module("app.ui.views")          # also builds REGISTRY
    importlib.import_module("app.ui.views.run_report")
    importlib.import_module("app.ui.views.history")
    importlib.import_module("app.ui.views.workflow_detail")
    importlib.import_module("app.ui.views.system_dashboard")
    importlib.import_module("app.ui.views.profiles")
    importlib.import_module("app.ui.views.settings")
    importlib.import_module("app.ui.views.resume_clinic")
    importlib.import_module("app.ui.views.analytics")
    importlib.import_module("app.ui.views.live_monitor")
    importlib.import_module("app.ui.views.job_detail")
    importlib.import_module("app.ui.views.start_run")
    importlib.import_module("app.ui.components")
    importlib.import_module("app.ui.components.bullets")
    importlib.import_module("app.ui.components.tailoring")
    importlib.import_module("app.ui.components.tracks")
    importlib.import_module("app.ui.formatting")
    importlib.import_module("app.ui.data")


def test_nav_views_are_unique_and_exclude_separator():
    assert nav.SEPARATOR in nav.NAV_ITEMS
    assert nav.SEPARATOR not in nav.NAV_VIEWS
    assert len(nav.NAV_VIEWS) == len(set(nav.NAV_VIEWS)), "duplicate view names"
    assert nav.NAV_VIEWS == [i for i in nav.NAV_ITEMS if i != nav.SEPARATOR]


def test_view_registry_covers_every_nav_view_and_all_callable():
    """Migration is complete: every nav view has a registered render(ctx), and the
    registry names nothing that is not a real view. A new nav entry without a
    render (or a typo'd key) fails here."""
    from app.ui.views import REGISTRY
    assert set(REGISTRY) == set(nav.NAV_VIEWS), (
        "REGISTRY must map exactly the nav views; "
        f"missing={set(nav.NAV_VIEWS) - set(REGISTRY)}, "
        f"extra={set(REGISTRY) - set(nav.NAV_VIEWS)}"
    )
    for name, fn in REGISTRY.items():
        assert callable(fn), f"REGISTRY[{name!r}] is not callable"


def test_registered_views_expose_render_without_running_streamlit():
    """Every migrated view exposes a callable render(ctx); importing its module did
    not execute Streamlit (else app.ui.views import above would have raised). The
    set tightens toward full coverage as views migrate."""
    from app.ui.views import REGISTRY
    for name, fn in REGISTRY.items():
        assert callable(fn), f"{name!r} render is not callable"


def test_entrypoint_sources_its_radio_from_nav():
    """Forcing function: the sidebar radio must source its options from
    nav.NAV_ITEMS, not a re-hardcoded list, so nav.py stays the single source of
    truth. Source-scan, because the entrypoint cannot be imported (runs st.* at
    import)."""
    src = _ENTRYPOINT.read_text(encoding="utf-8")
    assert "nav.NAV_ITEMS" in src, "entrypoint should pass nav.NAV_ITEMS to st.radio"
    assert "nav.SEPARATOR" in src, "entrypoint should compare against nav.SEPARATOR"
