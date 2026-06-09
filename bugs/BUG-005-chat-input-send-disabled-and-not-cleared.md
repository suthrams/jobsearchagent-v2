# BUG-005: Resume-clinic chat input — Send stays disabled while typing, box not cleared after send

- **Severity:** Medium (friction; blocks the chat flow until the user blurs the field)
- **Status:** Fixed
- **Reported:** 2026-06-08
- **Fixed:** 2026-06-08 (commit b819e82)
- **Area:** `app/ui/components/resume_chat_panel.py`
- **Introduced by:** ADR-068 (original chat panel)

## 1. What happened

In the Resume Clinic / tailoring live chat, two input defects:
1. **Send feedback never enabled** as the user typed — the button stayed greyed out, so
   it looked like you couldn't send a message at all.
2. After a successful send, **the previous message stayed in the text box**, so the box
   was not ready for the next message (the user had to clear it by hand).

## 2. Root cause

1. The Send button used `disabled=not _rc_message.strip()`, where `_rc_message` is the
   value of a Streamlit `text_area`. A `text_area` only **commits its value on blur or
   Ctrl+Enter**, not per keystroke — so no rerun fires while typing, the bound value
   stays empty, and `disabled` stays `True` until the user clicks away from the field.
   The gate fought Streamlit's input model.
2. Nothing reset the `text_area`'s session-state after a send. A Streamlit widget keyed
   by `key=` retains its value across reruns by design, so the sent text persisted.
   (You cannot clear it by assigning `st.session_state[key]` after the widget is
   instantiated in the same run — Streamlit forbids that — so a naive "clear after send"
   throws.)

## 3. Why it was not caught

Streamlit **UI interaction** has no automated coverage. The `smoke-test-ui` harness
renders each screen once via `AppTest` to prove `render()` doesn't raise — it never
types into a widget, never blurs a field, and never clicks a button and inspects the
follow-up state. Both defects are about *interaction timing* (type-without-blur) and
*cross-rerun widget state* (value persists), which a single render pass cannot observe.
Unit tests mock the API, not the Streamlit widget lifecycle.

## 4. Prevention

- **The fix:** removed the `disabled=` gate — Send is always clickable and validates on
  click (shows a hint if empty). The box clears via a pending-clear flag: the send
  handler sets `st.session_state[f"{key}__clear"] = True` and reruns; the next run
  honors it *before* the widget is instantiated (`if pop(flag): st.session_state[key] = ""`),
  which is the Streamlit-legal way to reset a widget value.
- **Forcing function:** none feasible at the unit level — the failure modes are
  Streamlit interaction timing and widget-lifecycle state, which `AppTest`'s
  single-render smoke pass cannot exercise. The fix is *structural* (no disabled gate to
  get stuck; the clear-flag pattern is the documented Streamlit idiom), so the specific
  failure modes are removed rather than merely guarded. Captured here + in b819e82 so the
  anti-patterns (disabled-on-uncommitted-value; post-instantiation widget mutation) are
  on record.
- **Generalization:** this is the same coverage gap as BUG-002 and BUG-006 — Streamlit
  interaction/navigation/widget-state bugs are invisible to render-only smoke tests. The
  durable mitigation is to prefer interaction-robust patterns (validate-on-click,
  before-instantiation resets, single-source-of-truth state) over clever widget gating.
