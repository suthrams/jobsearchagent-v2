"""Generic labelled-list / paragraph render helpers.

Phase 2 of the UI refactor (docs/architecture/ui_refactor_plan.md). These are the
small, reusable building blocks every view uses to render a labelled bullet list
or paragraph. They call ``st.*`` only inside the function body, so importing this
module does not render anything.
"""
from __future__ import annotations

import streamlit as st


def _bullets(label: str, items, *, sub: bool = False) -> None:
    """Render a labelled bullet list. No-op when items is empty / not a list.

    sub=True uses italic caption-style label for nested groups.
    """
    if not items or not isinstance(items, list):
        return
    if sub:
        st.markdown(f"_{label}_")
    else:
        st.markdown(f"**{label}**")
    for item in items:
        if isinstance(item, dict):
            # Best-effort flatten — render dicts as "key: value" bullets
            inner = "  ·  ".join(f"_{k}_: {v}" for k, v in item.items())
            st.markdown(f"- {inner}")
        else:
            st.markdown(f"- {item}")
    st.markdown("")  # blank line for breathing room


def _para(label: str, value) -> None:
    """Render a labelled paragraph. No-op when value is empty."""
    if not value:
        return
    st.markdown(f"**{label}**")
    st.write(value)
    st.markdown("")
