# BUG-006: Selected profile resets on page navigation (have to re-pick every time)

- **Severity:** High (wrong-profile data scoping — matches/history/resume picker can show
  another profile's data until the user notices and re-selects)
- **Status:** Fixed
- **Reported:** 2026-06-08
- **Fixed:** 2026-06-08
- **Area:** `app/ui/streamlit_app.py` (`_render_topbar` profile switcher, ADR-062)
- **Introduced by:** ADR-088 (move to native multipage `st.navigation`/`st.Page`) +
  ADR-062 (the profile switcher), interacting badly.

## 1. What happened

After selecting a profile in the top-right switcher, navigating to another page reverted
the active profile (often back to the first/`"0"`). The user had to re-select the right
profile on essentially every page change, and until they did, the page was scoped to the
wrong profile.

## 2. Root cause

The switcher had **two sources of truth** that could diverge:
- the selectbox's keyed widget state (`key="_profile_select"`), and
- the durable identity `st.session_state.current_user_id`.

The sync was a read-back: `_chosen = st.selectbox(..., index=_ids.index(current_user_id),
key="_profile_select"); if _chosen != current_user_id: current_user_id = _chosen`.

Under native multipage, `st.switch_page` runs the entrypoint fresh on the new page and the
**keyed widget state does not reliably carry the prior selection** across the switch (and
when a `key` and `index` are both set, Streamlit prefers the keyed value over `index`). So
on the destination page the selectbox produced a stale/default `_chosen`, which `!=
current_user_id`, and the read-back then **wrote that stale value back into
`current_user_id`** — actively reverting the active profile. `current_user_id` itself
persisted fine (its init is `setdefault`); the reverting read-back was the culprit.

## 3. Why it was not caught

No test exercises **profile-change-then-navigate**. The `smoke-test-ui` harness renders
each page once in isolation; it never selects a profile and then switches pages, so a
cross-page widget-state divergence is structurally invisible to it. There was also no
invariant pinning "`current_user_id` is the single source of truth," so the dual-state
read-back read as reasonable code.

## 4. Prevention

- **The fix:** `current_user_id` is the single source of truth. The selectbox is mirrored
  to it every run *before* instantiation (`if session_state["_profile_select"] != _cur:
  session_state["_profile_select"] = _cur`), so it always displays the active profile even
  if `st.switch_page` reset the widget state. The reverting read-back is removed; the only
  writer of `current_user_id` is an `on_change=_on_profile_change` callback, which fires
  *only* on a real user pick (not on the per-run mirror or a navigation reset), so
  navigation can never clobber the selection.
- **Forcing function:**
  `tests/v2/test_ui_structure.py::test_profile_switcher_is_single_source_of_truth` —
  source-scans the entrypoint to assert the `on_change` callback is used, the widget is
  mirrored to `current_user_id`, and the reverting `_chosen != current_user_id` sync is
  not reintroduced.
- **Generalization:** the guard pins the pattern for this widget; the broader lesson
  (shared with BUG-002/005) is that any widget whose value must survive `st.navigation`
  page switches needs a plain-session-state single source of truth driven by a callback —
  never a keyed-widget read-back. Render-only smoke tests cannot catch cross-page state
  bugs; treat "navigate after interacting" as a manual test step until an interaction
  harness exists.
