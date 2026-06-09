# BUG-007: Live Run Monitor never auto-refreshes (lambda passed to st.fragment)

- **Severity:** Medium (the "live" monitor isn't live — user must click Refresh to see progress)
- **Status:** Fixed
- **Reported:** 2026-06-08
- **Fixed:** 2026-06-08
- **Area:** `app/ui/views/live_monitor.py`
- **Introduced by:** commit e4bb336 (the feature shipped non-working — never live-verified)

## 1. What happened

After starting a search and landing on the Live Run Monitor, the activity feed,
status, and metrics did not update on their own. The "🔄 Live — auto-refreshing
every 5s" caption showed, but nothing refreshed; the user had to click **Refresh**
each time to see progress.

## 2. Root cause

The auto-refresh was wired as:

```python
st.fragment(lambda: _activity_body(wf_id, auto=True), run_every=5)()
```

`st.fragment` registers the `run_every` re-run timer keyed by the wrapped
function's identity. An **inline lambda is a brand-new object on every script
run**, so Streamlit never sees a stable fragment to keep alive — the timer is
attached to a throwaway lambda and is effectively orphaned each run, so it never
re-fires. The fragment renders once (on the run that created it) and then sits
static. The working Matches strip (`components/run_status.py`) uses a
**module-level** function (`_running_strip`), which has a stable identity, which
is why it refreshes and this did not.

## 3. Why it was not caught

`run_every` is a wall-clock timer that only fires in a live browser session. The
`smoke-test-ui` harness renders each screen once via `AppTest` and cannot advance a
timer or observe re-fires, so an auto-refresh that never fires looks identical to
one that works in a single-render test. Worse, the feature was **shipped without a
live click-through** (e4bb336) — the only thing that would have caught it. Same
coverage gap as BUG-002/005/006: interaction/timing behavior is invisible to
render-only tests.

## 4. Prevention

- **The fix:** pass a **stable module-level function** (`_auto_refresh_body`, which
  reads `workflow_id` from session_state) to `st.fragment(..., run_every=5)()`,
  mirroring the working `run_status` pattern. Stable identity -> the timer stays
  attached -> the body re-renders every 5s.
- **Forcing function:**
  `tests/v2/test_ui_structure.py::test_live_monitor_autorefresh_uses_stable_fragment`
  source-scans `live_monitor.py` to assert no `st.fragment(lambda` is used with
  `run_every`. (A real timer can't be unit-tested; this guards the specific
  reintroduction.)
- **Process:** auto-refresh / `run_every` / interaction features MUST get a live
  browser click-through before being called done — render-only smoke is necessary
  but not sufficient. Noted alongside the BUG-002/005/006 lesson.
- **Generalization:** any `st.fragment(..., run_every=...)` must take a stable
  (module-level) callable, never a lambda or a per-run closure.
