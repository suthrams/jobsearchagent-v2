# BUG-008: Profile selector STILL resets to primary on navigation (BUG-006 fix insufficient)

- **Severity:** High (wrong-profile data scoping; recurrence of BUG-006)
- **Status:** Fixed
- **Reported:** 2026-06-08 (after the BUG-006 fix shipped)
- **Fixed:** 2026-06-08
- **Area:** `app/ui/streamlit_app.py` (`_render_topbar` profile switcher)
- **Introduced by:** the BUG-006 fix (465bfdb) — it changed the symptom's code but not the behavior

## 1. What happened

After BUG-006 was "fixed" (selectbox `key` + `on_change` callback + a per-run mirror of
`current_user_id` into the widget key), the profile dropdown **still reverted to the
primary profile when navigating between pages**. The fix did not hold.

## 2. Root cause

The BUG-006 fix kept a **keyed widget with a fixed key (`_profile_select`)** and drove
identity through an `on_change` callback. Under `st.navigation`, that combination is
unreliable: Streamlit reuses the fixed-key widget's prior (page-scoped) state across
`st.switch_page` and **ignores the `index`**, and the `on_change` path then commits the
reset value back into `current_user_id`. The pre-run "mirror" (`st.session_state
["_profile_select"] = _cur`) did not reliably override the widget's reused state.
`current_user_id` itself (a plain session key) persisted; the widget display + callback
were what dragged it back.

The deeper mistake: I diagnosed BUG-006 from a *theory* of Streamlit behavior and never
verified it across real page navigation, so the fix targeted the wrong mechanism.

## 3. Why it was not caught

The BUG-006 forcing function was a **source scan** (`test_ui_structure` asserted
`on_change=_on_profile_change` was present). It verified the code *shape*, not the
runtime behavior — it would pass whether or not the profile actually stuck. Render-only
smoke (`AppTest`) renders each page once and cannot perform a profile-change-then-
navigate sequence. So both guards were blind to the actual regression: a source scan
that pins the wrong pattern is worse than none, because it reads as "covered."

## 4. Prevention

- **The fix:** make `current_user_id` the sole source of truth and DISPLAY-drive the
  selectbox from it via `index`, with the widget **`key` derived from `current_user_id`
  (`f"_profile_select::{_cur}"`)**. A key that changes with the source of truth makes
  each profile a *fresh* widget with no stale state, so Streamlit honors `index` and the
  box always shows the active profile across navigation. Identity is written only when
  the returned value differs (`if _choice != current_user_id`). No `on_change`.
- **Forcing function:** `test_ui_structure.py::test_profile_switcher_is_single_source_of_truth`
  rewritten to assert the new invariant (index-driven display, current_user_id-derived
  key, write-from-returned-choice, no `on_change`). Still a source scan — see below.
- **Process (the real fix):** UI behavior that depends on `st.navigation` widget-state
  or `run_every` timers MUST get a **live browser click-through** before "done";
  render-only smoke + source scans cannot see it. This is the recurring lesson from
  BUG-002/005/006/007 and now BUG-008. Do not ship a navigation/interaction fix as
  verified on the strength of a source scan alone.
- **Generalization:** for any Streamlit widget whose value must survive page
  navigation, derive its `key` from the persistent source of truth and reconcile from
  the return value; avoid fixed-key + on_change for cross-page identity.
