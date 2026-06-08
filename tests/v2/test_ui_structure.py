"""Structural invariants for the Streamlit UI (ui_refactor_plan.md + ADR-088).

Forcing functions (cf. the model-pin and PII-redaction invariants): they fail the
build if the view registry drifts from the nav set, if a view module stops exposing
a callable render(), if importing a view module executes Streamlit at import time,
or if the entrypoint stops sourcing its journey navigation from nav.py.

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
    importlib.import_module("app.ui.views.matches")
    importlib.import_module("app.ui.views.live_monitor")
    importlib.import_module("app.ui.views.opportunity")
    importlib.import_module("app.ui.views.start_run")
    importlib.import_module("app.ui.components")
    importlib.import_module("app.ui.components.bullets")
    importlib.import_module("app.ui.components.tailoring")
    importlib.import_module("app.ui.formatting")
    importlib.import_module("app.ui.data")


def test_nav_views_are_grouped_plus_destinations_unique():
    """NAV_VIEWS is exactly the grouped sidebar views (in group order) followed by
    the hidden destinations, with no duplicates and no overlap (ADR-088)."""
    grouped = [name for group in nav.NAV_GROUPS.values() for name in group]
    assert nav.NAV_VIEWS == grouped + list(nav.DESTINATION_VIEWS)
    assert len(nav.NAV_VIEWS) == len(set(nav.NAV_VIEWS)), "duplicate view names"
    assert not (set(grouped) & set(nav.DESTINATION_VIEWS)), (
        "a view cannot be both a sidebar view and a hidden destination"
    )


def test_default_landing_is_a_sidebar_view():
    """ADR-088 D: the app lands on Matches, which must be a real grouped view (not a
    hidden destination, which has no sidebar entry to default to)."""
    grouped = [name for group in nav.NAV_GROUPS.values() for name in group]
    assert nav.DEFAULT_VIEW in grouped
    assert nav.DEFAULT_VIEW == "Matches"


def test_display_titles_cover_every_view_and_are_unique():
    """Every view has a user-facing title (st.Page title + url_path), and titles are
    unique so the derived url_paths do not collide."""
    assert set(nav.DISPLAY_TITLE) == set(nav.NAV_VIEWS), (
        "DISPLAY_TITLE must name exactly the nav views; "
        f"missing={set(nav.NAV_VIEWS) - set(nav.DISPLAY_TITLE)}, "
        f"extra={set(nav.DISPLAY_TITLE) - set(nav.NAV_VIEWS)}"
    )
    titles = list(nav.DISPLAY_TITLE.values())
    assert len(titles) == len(set(titles)), f"duplicate display titles: {titles}"


def test_chrome_drops_workflow_vocabulary():
    """ADR-088 goal: a job seeker never sees 'Workflow' in the chrome. The internal
    route names may keep it (invisible plumbing), but no user-facing title may."""
    offenders = [t for t in nav.DISPLAY_TITLE.values() if "workflow" in t.lower()]
    assert not offenders, f"display titles still say 'Workflow': {offenders}"


def test_view_registry_covers_every_nav_view_and_all_callable():
    """Every nav view (grouped + destination) has a registered render(ctx), and the
    registry names nothing that is not a real view. A new view without a render (or
    a typo'd key) fails here."""
    from app.ui.views import REGISTRY
    assert set(REGISTRY) == set(nav.NAV_VIEWS), (
        "REGISTRY must map exactly the nav views; "
        f"missing={set(nav.NAV_VIEWS) - set(REGISTRY)}, "
        f"extra={set(REGISTRY) - set(nav.NAV_VIEWS)}"
    )
    for name, fn in REGISTRY.items():
        assert callable(fn), f"REGISTRY[{name!r}] is not callable"


def test_registered_views_expose_render_without_running_streamlit():
    """Every view exposes a callable render(ctx); importing its module did not
    execute Streamlit (else the app.ui.views import above would have raised)."""
    from app.ui.views import REGISTRY
    for name, fn in REGISTRY.items():
        assert callable(fn), f"{name!r} render is not callable"


def test_opportunity_page_has_no_application_tracking():
    """ADR-088 section E + CLAUDE.md 'no application tracking': the Opportunity page
    offers preparation (tailor, interview) + filtering (exclude/hide) only. It must
    not introduce Apply / Save / application-status controls, nor a complementary
    pursuing / shortlist / saved set (back-door application tracking). Scan the
    module's string literals EXCEPT the module/function docstrings, so the design
    rationale can still name the forbidden concepts while the UI must not use them."""
    import ast

    opp = _ENTRYPOINT.parent / "views" / "opportunity.py"
    tree = ast.parse(opp.read_text(encoding="utf-8"))
    # Identify docstring Constant nodes by identity (the first statement of a
    # module/function/class body), so the design rationale can name the forbidden
    # concepts while the rendered UI strings must not.
    doc_node_ids = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                doc_node_ids.add(id(first.value))
    blob = " ".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in doc_node_ids
    ).lower()
    forbidden = ["pursuing", "shortlist", "applied", "application status",
                 "mark as applied", " save ", " apply "]
    hits = [w for w in forbidden if w in blob]
    assert not hits, f"Opportunity page UI text must not imply application tracking: {hits}"


def test_every_destination_has_a_navigation_entry_point():
    """ADR-088: hidden destinations have no sidebar entry, so each MUST be a
    _navigate(...) target somewhere in the UI - otherwise it is reachable only by
    typing its URL (the regression that left Run report orphaned after the reorg).
    Source-scan every app/ui module for a _navigate("<dest>") call."""
    ui_dir = _ENTRYPOINT.parent
    srcs = "\n".join(
        p.read_text(encoding="utf-8")
        for p in ui_dir.rglob("*.py")
        if p.name != "nav.py"  # nav.py defines _navigate; callers live elsewhere
    )
    for name in nav.DESTINATION_VIEWS:
        assert f'_navigate("{name}"' in srcs, (
            f"destination {name!r} has no _navigate entry point - it would be "
            "reachable only by URL. Wire a button/row that navigates to it."
        )


def test_destination_views_render_an_in_app_back():
    """ADR-088 F / UX-review R-1: every hidden destination needs an explicit in-app
    Back, because under native multipage the browser Back button misleads. Source-scan
    each destination's module for a back_button() call."""
    import inspect

    from app.ui.views import REGISTRY
    for name in nav.DESTINATION_VIEWS:
        mod = inspect.getmodule(REGISTRY[name])
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "back_button(" in src, (
            f"destination {name!r} must render an in-app Back (nav.back_button)"
        )


def test_cross_run_filters_are_contextual_to_matches():
    """ADR-088 Phase 3: the min-score / search / include-excluded controls render on
    Matches (the one screen that consumes them), not as always-on sidebar widgets.
    Source-scan both files."""
    entry = _ENTRYPOINT.read_text(encoding="utf-8")
    matches = (_ENTRYPOINT.parent / "views" / "matches.py").read_text(encoding="utf-8")
    assert "Minimum match score" not in entry, (
        "the global sidebar must not render the min-score filter (it moved to Matches)"
    )
    assert "Minimum match score" in matches, "Matches must render the min-score filter"
    # The persistent mirror keys survive navigation for _build_ctx / New search.
    assert "flt_min_score" in entry, "entrypoint must seed/read the flt_* mirror keys"


def test_entrypoint_uses_native_multipage_sourced_from_nav():
    """Forcing function: the entrypoint builds native-multipage navigation
    (st.navigation / st.Page) from nav.py's journey structure, registers the pages
    so _navigate can switch, and no longer hardcodes a sidebar radio. Source-scan,
    because the entrypoint cannot be imported (runs st.* at import)."""
    src = _ENTRYPOINT.read_text(encoding="utf-8")
    assert "st.navigation" in src, "entrypoint should build native multipage navigation"
    assert "st.Page" in src, "entrypoint should wrap each view in an st.Page"
    assert "nav.NAV_GROUPS" in src, "journey groups must come from nav.NAV_GROUPS"
    assert "nav.DESTINATION_VIEWS" in src, "hidden destinations must come from nav.py"
    assert "nav.DISPLAY_TITLE" in src, "page titles must come from nav.DISPLAY_TITLE"
    assert "register_pages" in src, "entrypoint must register pages for _navigate"
