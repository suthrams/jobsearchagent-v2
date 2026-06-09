# BUG-009: Live run unreachable from other screens after the sidebar declutter

- **Severity:** Medium (a running search can't be watched from most screens)
- **Status:** Fixed
- **Reported:** 2026-06-08
- **Fixed:** 2026-06-08
- **Area:** `app/ui/components/run_status.py` (`_chip`)
- **Introduced by:** a971022 (sidebar declutter)

## 1. What happened

After triggering a new search, the user couldn't find a link to the live run from the
screen they were on — there was no way to reach the Live Run Monitor.

## 2. Root cause

The sidebar declutter (a971022) stripped **all** buttons from the run-status chip,
including the running-state "Watch live ▶". The Live Run Monitor is a **hidden
destination** (no entry in the native nav, by design — ADR-088 F). Its only entry
points were the chip's "Watch live" button and the Matches strip's "Watch" button. With
the chip button gone, any screen that wasn't Matches had **no path to the live run**.
(Starting a search auto-navigates there, but once the user navigated away there was no
way back.)

## 3. Why it was not caught

`test_every_destination_has_a_navigation_entry_point` checks that each hidden
destination is *some* `_navigate(...)` target — and "Live Run Monitor" still had one
(the Matches strip), so the invariant passed. It does not assert "reachable from *any*
screen while a run is active." Render-only smoke renders each page in isolation and
never exercises the "start a run, navigate away, get back to it" path. Same coverage
gap class as BUG-008.

## 4. Prevention

- **The fix:** restore a "Watch live ▶" jump in the sidebar chip **only while a run is
  active** (`status in {running, cancelling}`). Idle/done states stay text-only
  (the declutter intent holds); the live run is now reachable from every screen during
  a run.
- **Forcing function:** none added beyond the existing destination-reachability test —
  "reachable from any screen while running" is a runtime/navigation property that the
  render-only harness can't assert. Mitigated by keeping the active-run jump in the
  always-present sidebar chip.
- **Generalization:** when decluttering, a control that is the *sole* path to a hidden
  destination must not be removed without providing another path. Hidden destinations
  need a durable, always-present entry point for the state in which they matter.
