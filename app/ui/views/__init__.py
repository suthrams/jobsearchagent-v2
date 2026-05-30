"""Per-screen Streamlit views for the v2 UI.

Phase 0 of the UI refactor (docs/architecture/ui_refactor_plan.md). Each screen
becomes its own module here exposing a single ``render() -> None`` that performs
all of that view's ``st.*`` calls. Importing a view module must NOT execute any
Streamlit call (the body lives inside ``render()``), so the structure tests can
import every view without a Streamlit runtime.

Empty for now: the views still live inline in streamlit_app.py and migrate here
one at a time (Phases 3-4), each registered in app/ui/nav.py::VIEW_REGISTRY.
"""
