# ADR-089: Matches as the Live Home Base

## Status

Accepted (2026-06-07). Builds directly on ADR-088 (the job-seeker journey reorg).
Implemented in the same session.

## Context

ADR-088 reorganized the UI around the job-seeker journey and made **Matches** the
landing screen. Living on it surfaced a remaining friction the reorg did not fix:
the **run lifecycle still makes the user bounce between screens**, and the run state
is presented in system vocabulary.

Concretely, after **New search -> Start** the user got a static success message and a
"Watch live" button to a *separate* Live monitor screen, then had to navigate back to
Matches and press **Refresh data** to see results. One logical activity ("run a
search and see what came back") spanned three screens and a manual refresh. The
sidebar **Active Run** panel compounded it: it auto-reconnects to the most recent run
and labels even a *completed* run "Active Run", then shows the same three system
buttons (Detail / Live / Report) regardless of whether the run is running, done, or
failed - the right action is different per state, but the panel never adapts.

The result is two parallel mental models fighting each other: a *job-centric* world
(Matches -> Opportunity) and a *run-centric* world (Searches -> Search detail -> Live
monitor -> Run report). A job seeker thinks in jobs; half the screens are about runs.

Streamlit 1.56 makes the fix newly cheap: `st.fragment(run_every=...)` gives partial
auto-refresh (closing the deferred ADR-088 UX-review R-7), and a real `cancel`
endpoint exists (ADR-083).

## Decision

**Make Matches the live home base.** Starting a search, watching it run, and seeing
results all happen on Matches. The run-centric screens become optional drill-downs,
never required for the core loop.

### A. A state-aware run-status strip on Matches

A new shared `app/ui/components/run_status.py` renders the run state, in job-seeker
words, at the top of Matches (full strip) and in the sidebar (a slim chip linking to
Matches, so run state is visible from any screen). It branches on the run's status:

- **Idle** (no run) -> `[ + New search ]`.
- **Running** (blue) -> friendly step + elapsed + calls + `$cost` (all from the status
  response's `run_metrics`), with `[ Watch ]` (Live monitor) and `[ Cancel ]`
  (ADR-083).
- **Awaiting scoring selection** (yellow, ADR-060) -> `[ Choose jobs to score ]` -
  surfacing the manual-selection entry the ADR-088 UX review (R-5) wanted here.
- **Done** (green) -> "Last search done" + `[ Report ]` + `[ + New search ]`.
- **Failed** (red) -> `[ What happened ]` + `[ + New search ]`.

### B. Auto-refresh while running (closes R-7)

While the run is `running`, the strip renders inside `st.fragment(run_every=5s)` and
re-polls `get_workflow_status`. When it detects the run left `running`, it clears the
read caches and calls `st.rerun(scope="app")` so the full page refreshes and results
appear - no manual Refresh. Polling happens **only** while running (the fragment is
not used in any other state), and it hits the local API only (no LLM cost).

### C. Results appear inline, badged "new"

Rows scored by the most recent run are flagged **NEW** on the Matches table, so the
payoff of a just-finished search is obvious without leaving the screen.

### D. New search reclaims the flow

After **Start**, New search navigates back to **Matches** (instead of the static
"Watch live" message). The strip then drives the rest. The New search *form* stays its
own page (it is config-heavy - titles, locations, ADR-060/065/079/080 opt-ins);
Matches just launches it (`[ + New search ]`) and reclaims the flow on submit.

### E. The run-centric screens demote, they do not disappear

**Searches** stays in the FIND group as a secondary run log (the home of "re-run a
search with tweaks"); it is no longer part of the core loop. Live monitor, Search
detail, and Run report remain hidden click-through destinations (ADR-088 F), reached
from the strip's Watch / Report / What-happened actions and from Searches rows. The
sidebar **Active Run** panel is replaced by the slim status chip (the rich, state-
aware panel now lives inline on Matches).

### F. Guardrails / non-goals

- **No application tracking** (CLAUDE.md / ADR-088 E). The strip offers *preparation*
  and *navigation* only - no Apply/Save/status, no pursuing/shortlist/saved set.
- **No backend change.** Reuses existing reads + the `cancel` endpoint (ADR-083);
  reads still go through the API (ADR-075).
- **No new agent, node, or workflow change** - purely presentation.

## Options considered

- **Keep ADR-088's structure, just fix the Active Run panel.** Rejected as
  insufficient: it leaves the New search -> Live monitor -> Matches bounce and the
  two-worlds split intact.
- **Auto-land on a dedicated live-progress screen after Start.** A real improvement,
  but it keeps the run as a separate destination; the user still leaves Matches. The
  inline strip keeps the single home base.
- **Fold Searches into Matches entirely** (a "by search" view). Deferred: it loses the
  clean run-history list and adds weight to Matches for little core-loop gain.
- **Inline the run onto Matches (chosen).** Maximizes the core-loop payoff: near-zero
  navigation from "start a search" to "act on a match", with the run-centric screens
  still one click away when wanted.

## Consequences

### Positive

- The core loop (search -> watch -> results -> act) happens on one screen with no
  manual refresh and no screen-bouncing.
- Run state is state-aware and in job-seeker words; the right action shows per state.
- Closes the deferred ADR-088 R-7 (async completion) and folds in R-5 (manual-
  selection entry) on the way.

### Tradeoffs

- `st.fragment(run_every)` polls the API every 5s while a run is active (API only, no
  LLM). Bounded to the running state.
- Matches takes on the run-status responsibility, so it must not become a new
  mega-page; the strip is a thin component and anything run-detailed stays on the
  drill-down screens.

### Neutral

- Docs: this ADR + index; `ui_architecture.md` (the strip + auto-refresh + sidebar
  chip); `user_guide.md` (the new flow); `ui_journey_reorg_plan.md` cross-reference.
- Tests: `test_ui_structure` (Matches renders the strip; the no-tracking scan extends
  to the component); a New-search-lands-on-Matches assertion; smoke covers the strip.

## References

- ADR-088 - the journey reorg this builds on (Matches as landing; hidden
  destinations; UX-review R-5 manual-selection entry and R-7 async completion).
- ADR-083 - cooperative cancellation (the Cancel action).
- ADR-060 - manual scoring selection (the "Choose jobs to score" state).
- ADR-075 - UI reads through the API (unchanged).
- CLAUDE.md "No application tracking" / [[feedback_filter_vs_tracker_distinction]].
