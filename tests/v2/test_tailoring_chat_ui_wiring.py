"""ADR-072 T6: structural guards for the shared chat panel wiring.

The chat+export panel was extracted from the Resume Clinic view into a shared
component (resume_chat_panel) reused by both the clinic view and the tailoring
card. These source-scan invariants (cf. test_ui_structure / test_ui_undefined_names)
fail the build if the extraction regresses: if the panel stops being shared, if a
view inlines it again, or if the component loses its render entry point.

ADR-088 Tier 2: the tailoring chat moved from Workflow Detail to the Opportunity
page. ADR-090: the per-job tailoring flow (trigger + drafts + decisions + chat) was
extracted into the shared components/tailoring_panel.py, reused by the Opportunity
page AND the job-focused Resume Clinic - so the chat wiring now lives there.
"""
from __future__ import annotations

from pathlib import Path

_UI = Path(__file__).resolve().parents[2] / "app" / "ui"
_COMPONENT = _UI / "components" / "resume_chat_panel.py"
_CLINIC = _UI / "views" / "resume_clinic.py"
_TAILORING_PANEL = _UI / "components" / "tailoring_panel.py"


def test_component_exposes_render_chat_panel():
    src = _COMPONENT.read_text(encoding="utf-8")
    assert "def render_chat_panel(" in src
    # The panel owns the chat + export blocks (the actual rendered subheaders).
    assert 'st.subheader("Refine with feedback")' in src
    assert 'st.subheader("Export the final resume")' in src


def test_component_imports_clean():
    import importlib
    mod = importlib.import_module("app.ui.components.resume_chat_panel")
    assert callable(mod.render_chat_panel)


def test_both_views_use_the_shared_panel():
    for view in (_CLINIC, _TAILORING_PANEL):
        src = view.read_text(encoding="utf-8")
        assert "render_chat_panel(" in src, f"{view.name} must call the shared panel"


def test_clinic_view_no_longer_inlines_the_chat_block():
    # The chat block moved to the component; the view must not also render it
    # (that would be the drift we extracted to prevent). Check the actual
    # subheader calls, not the words in a call-site comment.
    src = _CLINIC.read_text(encoding="utf-8")
    assert 'st.subheader("Refine with feedback")' not in src
    assert 'st.subheader("Export the final resume")' not in src


def test_tailoring_card_opens_chat_outside_expander():
    # The "Open live chat" entry point lives next to the tailoring card in the shared
    # tailoring panel, and the panel renders via the active-session key (outside any
    # expander, since the card itself contains expanders and Streamlit forbids
    # nesting them).
    src = _TAILORING_PANEL.read_text(encoding="utf-8")
    assert "Open live chat" in src
    assert "tail_chat_active_tid" in src
    assert "open_tailoring_chat_session" in src
