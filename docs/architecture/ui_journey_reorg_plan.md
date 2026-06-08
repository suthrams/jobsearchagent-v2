# UI Journey Reorganization - Implementation Plan, Wireframes & Workflows

Companion to [ADR-088](adr/ADR-088-reorganize-ui-around-job-seeker-journey.md).
This is the artifact to review **before** any code is written: it shows the
proposed screens (wireframes), how a user moves through them (engagement
workflows), the phased build, the file-level changes, the test plan, and the
recommendations with effort/impact.

Status: Accepted, in progress. **Done:** Phase 2 (merged Matches) + Phase 0 (native
multipage nav, rename, hidden destinations, land on Matches) + Phase 3 (contextual
filters - moved into Matches) + Phase 4 (in-app Back on every destination). **Tier 1
complete. Open:** Phases 5, 6 (Tier 2).

**Decisions locked (2026-06-07):** framework = Streamlit + native multipage
(`st.navigation`/`st.Page`) + fragments (section 11); scope = both tiers (phases
0-6); primary user = job seeker (operator screens below a rule); mockups rendered
and signed off before implementation. These resolve open questions O-1 and O-2.

---

## 1. Goal

Reorient the UI from a system-centric layout (15 flat nav items named after
"workflows") to a journey-centric one a job seeker can use without translating
their goal into the system's vocabulary. Two tiers:

- **Tier 1 (quick wins):** nav reorg, merged Matches screen, contextual filters,
  journey landing, operator drawer. Mostly `nav.py` + view consolidation behind the
  existing `render(ctx)` + `REGISTRY` dispatch. No backend change.
- **Tier 2 (bigger bet):** a job-centric Opportunity page as the single per-job
  surface; Workflow Detail shrinks to a run summary.

Every existing capability is preserved. No backend, identity, agent, or workflow
change (ADR-088 section H).

---

## 2. Navigation: before -> after

```
CURRENT (nav.py:32-49)                  PROPOSED (revised after UX review, sec 10)
------------------------------          --------------------------------------
[ Profile v ]                           [ Profile v ]
View (radio, 15 items):
  Workflow History     (landing)        FIND
  Workflow Detail      (dead-end)         New search
  Job Detail           (dead-end)         Searches     (was Workflow History)
  Start New Run
  Live Run Monitor                       MY OPPORTUNITIES
  Run Report                               Matches      <- landing, track-aware,
  Resume Clinic                                            Companies as an in-page tab
  Settings
  Profiles                              RESUME
  --- Cross-Run Analytics ---             Resume Clinic
  System Dashboard                        Profiles & Resumes
  Top Matches
  IC Track                              ----------------- (rule, no "MANAGE" noun)
  Architect Track                         Settings
  Management Track                        Spend & Health  (was System Dashboard)
  Companies
                                        Destinations (not in nav; reached by click,
[min score][search][excluded]            with an in-app Back - the browser Back
[refresh][Active Run]                     will mislead, see UX note R-1): Opportunity
                                          (job), Search detail (run), Live monitor,
                                          Run report
                                        Filters render only on Matches / Searches
                                        [refresh][Active Run]  (kept; also hosts the
                                          "Pick jobs to score" entry, see 4.3)
```

> UX-review changes baked in here (section 10): **Searches moved to FIND** (it is a
> search-log, not an opportunity); **Companies is an in-page tab of Matches**, not a
> peer track segment (different layout, not a re-sort); the operator group is a plain
> rule, not a "MANAGE" noun (we removed "Workflow" vocabulary - don't add "Manage").

---

## 3. Wireframes

ASCII, low-fi, intentionally about layout and hierarchy, not pixels.

### 3.1 Sidebar (Tier 1)

```
+--------------------------------+
| Job Search Agent               |
| Profile: [ Priya  (#1)    v ]  |
| [ + Add profile ]              |
|--------------------------------|
| FIND                           |
|   o New search                 |
|                                |
| MY OPPORTUNITIES               |
|   * Matches                    |   <- selected (default landing)
|   o Searches                   |
|                                |
| RESUME                         |
|   o Resume Clinic              |
|   o Profiles & Resumes         |
|--------------------------------|   <- rule, no operator noun
|   o Settings                   |
|   o Spend & Health             |
|--------------------------------|
| Active Run  (o) running        |   <- unchanged widget
|  9b2f1c...  step: score_jobs   |
|  6 calls  $0.0123              |
|  [ Detail ]   [ Live ]         |
|--------------------------------|
| [ Refresh data ]               |
+--------------------------------+
```

### 3.2 Matches - the home base (Tier 1, merges 5 screens)

Interaction (UX-review fix R-2): Streamlit's `st.dataframe` **cannot** render an
in-row button or in-app link. The only proven pattern in the codebase is
select-a-row (`on_select="rerun"`) then act via a button cluster
(`workflow_detail.py:302-356`). Matches reuses exactly that - no clickable "Open >"
cells.

```
+-------------------------------------------------------------------------+
| Matches                                          Priya (#1)             |
| Your best opportunities across every search, newest scores first.       |
| 14 scored  *  8 above 75  *  6 companies                                |  <- context, above controls
|                                                                         |
| View:  [ Roles ] [ Companies ]            <- st.tabs (different layouts) |
| Sort:  ( Best fit ) ( IC ) ( Architecture )  <- segmented_control, only |
|                                                 the profile's ACTIVE     |
|                                                 tracks (ADR-071)         |
| Filters: min score [=====75===]   search [ Staff Engineer  ]  [x] excl  |
|-------------------------------------------------------------------------|
|  o | Score | Role                | Company | Loc    | Posted | Summary   |
|  ( )| [ 86 ]| Staff Engineer     | Acme    | Remote | 3d     | strong... |
|  (*)| [ 82 ]| Principal Architect| Globex  | ATL    | 6d     | aligns... |  <- selected row
|  ( )| [ 78 ]| Platform Lead      | Initech | Remote | 1d     | partial...|
|-------------------------------------------------------------------------|
|  Selected: Principal Architect - Globex                                 |
|     [ Open opportunity > ]   [ Exclude ]                                 |  <- act on selection
+-------------------------------------------------------------------------+
```

"Companies" is the **Companies tab** (the existing bar chart + grouped table from
`render_companies`), not a sort option - keeping a layout-toggle out of the sort
control (UX-review R-3). The `match_summary` / `recommended_next_action` text
columns the per-track tables show today move to the Opportunity page; noted in the
capability checklist (risk 8.2).

First-run empty state (UX-review R-6: branch on whether a resume exists):

```
+----------------------------------+   +----------------------------------+
| Matches                          |   | Matches                          |
|                                  |   |                                  |
|  No resume on this profile yet.  |   |  No searches yet.                |
|  Scoring needs a resume to       |   |  Find roles scored against your  |
|  compare roles against.          |   |  resume across your tracks.      |
|                                  |   |                                  |
|   [ Add your resume ]            |   |   [ Start your first search ]    |
+----------------------------------+   +----------------------------------+
   (profile has no resume)               (resume present, no runs)
```

### 3.3 Opportunity page - one job, all actions (Tier 2)

Scope note (UX-review R-4): the Opportunity page must absorb the **full** tailoring
flow from `workflow_detail.py:481-617` - drafts list, approve/revise/reject/edit
decisions, and the ADR-072 live-chat panel - plus the on-demand **cost / "already
run" legibility** (the "extra cost: runs deep review first" warning at
`workflow_detail.py:541-545` is load-bearing and must survive). Otherwise the
tailoring flow splits across two screens, worse than today. This roughly doubles
the naive "merge Job Detail + action expanders" estimate.

```
+-------------------------------------------------------------------------+
| [ < Back to Matches ]   (browser Back will mislead - use this)         |
|                                                          [ Exclude ]    |  <- de-emphasized; "Hide from searches"
|  Staff Engineer   -   Acme                                              |
|  Best track: Architecture 82   *   Posted 6d ago   *   Remote          |
|  small caption: found by search "Staff / Principal" on 2026-06-05      |
|-------------------------------------------------------------------------|
|  WHY IT FITS                            GAPS                            |
|  - Strong platform + IaC match          Resume gaps (have it, not       |
|  - Leadership signal aligns               documented): on-call ownership |
|  Overall 80  IC 74  Arch 82             Career gaps (don't meet):       |
|                                           formal people mgmt            |
|-------------------------------------------------------------------------|
|  DEEP REVIEW                       [ Run review ~$0.04 ]  or  done v     |
|-------------------------------------------------------------------------|
|  NEXT STEPS                                                             |
|   [ Tailor my resume ~$0.02 ]      [ Prep for interview ~$0.03 ]        |
|     ! tailoring a non-selected job runs deep review first (extra cost)  |
|                                                                         |
|   Tailored drafts (newest first)                                       |
|    > Draft 3  approved   strategy / impact / fidelity / diffs   [open]  |
|    > Draft 2  rejected                                          [open]  |
|   [ Generate new draft ]      [ Live chat to refine + export ] (ADR-072)|
|                                                                         |
|   NOTE: no Apply/Save/status/"pursuing" set here by design - the page   |
|   offers prep (tailor, interview) + filter (exclude) only.              |
+-------------------------------------------------------------------------+
```

### 3.4 Searches - the run list (Tier 1, was Workflow History)

```
+-------------------------------------------------------------------------+
| Searches                                                Priya (#1)      |
| Every search you've run, newest first. Click to see that run.          |
|-------------------------------------------------------------------------|
|  When        | Roles / Locations        | Scored | Qualified | Cost    |
|  2026-06-05  | Staff/Principal +2  Remote|   10   |     4     | $0.11   |
|  2026-06-01  | Platform Lead       ATL   |    8   |     2     | $0.08   |
|-------------------------------------------------------------------------|
| Click a row -> Search detail (run summary + jump to a job).            |
+-------------------------------------------------------------------------+
```

### 3.5 Search detail (Tier 2 shrink of Workflow Detail)

```
+-------------------------------------------------------------------------+
| [ < Back to Searches ]                          run 9b2f1c...  $0.11    |
| Search "Staff / Principal - Remote"  *  completed  *  2026-06-05       |
|-------------------------------------------------------------------------|
|  Settings used  [v]   Limits hit  [v]   Diagnostics (operator)  [v]    |
|-------------------------------------------------------------------------|
|  Jobs in this run            (click any -> Opportunity page)            |
|  [ 86 ] Staff Engineer  Acme        qualified   Open >                  |
|  [ 62 ] Backend Eng     Initech     below thr   Open >                  |
+-------------------------------------------------------------------------+
```

The current mega-page's per-job Review / Prep / Tailoring blocks move to the
Opportunity page (3.3); the run page keeps only run-level summary + the job list.
When the run is parked at `awaiting_scoring_selection` (manual selection, ADR-060),
this same page shows the **"Select jobs to score" picker** variant - and that entry
is *also* surfaced directly from the Active Run widget (UX-review R-5, see 4.3) so
curate-before-score does not regress into run-drilling.

---

## 4. User engagement workflows

### 4.1 First-time user (new profile, no data)

```
Add profile (wizard: identity -> resume -> default roles)
   -> lands on Matches (empty state, branched on resume present?)
   -> "Start your first search"
   -> New search (pre-filled from wizard defaults) -> Start
   -> Active Run widget shows progress; [Live] for the feed
   -> when the run finishes, new scores appear on Matches on the next
      refresh/return (Streamlit cannot push - UX-review R-7; while a run is
      active we offer a gentle auto-refresh so the user is not stranded on a
      stale empty state)
```

### 4.2 Returning user - the core loop

```
Open app -> Matches (default)
   -> scan top rows / switch segment (Best fit | IC | Arch | Companies)
   -> click a promising row -> Opportunity page
   -> read fit + gaps + deep review
   -> Tailor my resume  AND/OR  Prep for interview
   -> Back to Matches; repeat for the next role
```

Three clicks from open to acting on a job (open -> row -> action), vs ~5 today.

### 4.3 "Curate before I pay to score" (manual selection, ADR-060)

```
New search -> tick "Let me pick which jobs to score" -> Start
   -> Active Run parks (awaiting_scoring_selection)
   -> Active Run widget shows [ Pick jobs to score ]  (UX-review R-5: surfaced
      directly, NOT buried under Searches -> run -> picker)
   -> pick -> submit -> scoring runs -> Matches refreshes
```

### 4.7 Re-run a prior search with tweaks (UX-review: missing high-value flow)

```
Searches -> open a run -> "Run again with tweaks"
   -> New search pre-filled from that run's search_criteria
   -> adjust (e.g. drop the salary floor) -> Start
```

> Future (not v1, but the IA must not preclude it): **compare two opportunities**
> side by side - the natural extension of a Matches screen whose whole job is
> helping the user choose.

### 4.4 Improve the resume itself (no specific job)

```
Resume Clinic -> pick resume (+ optional target role/track)
   -> Run clinic -> read scorecard / alignment / rewrites
   -> chat-revise to refine (ADR-068) -> Approve/Edit
   -> Export (md/docx/pdf/...)
```

### 4.5 Operator / builder monitoring (separated, not in the job path)

```
MANAGE -> Spend & Health  (cost, security, latency, reliability; ADR-073)
MANAGE -> Settings        (search defaults, tracks, agent models, retention)
```

### 4.6 Cancel a long run

```
Active Run widget [Live] -> Live monitor -> Cancel
   -> cooperative stop at next node boundary (ADR-083)
```

---

## 5. Phased implementation

Each phase is independently shippable, behind the existing dispatch, with a green
test suite + UI smoke test before the next.

Order revised after the UX review (section 10): pure rename first, the fragile
click-through demotion last in Tier 1, and the Matches merge before the filters
that now live inside it.

| Phase             | Scope                                                                                                                                                                                                                                                                                                                      | Primary files                                                                                        | Tests                                                      |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 0 **(DONE)**      | Adopt `st.navigation`/`st.Page` so the journey groups (FIND / MY OPPORTUNITIES / RESUME / rule) become real routable pages; rename + regroup; hidden click-through destinations; land on Matches. Internal view names kept stable; only `DISPLAY_TITLE` renames. Operator group is a rule-glyph header (no "MANAGE" noun). | `nav.py`, `streamlit_app.py`, `test_ui_structure`, `smoke_ui.py`                                     | `test_ui_structure` rewritten; smoke 12/12                 |
| 1 (folded into 0) | Land on Matches via `default=True`; branched empty state already in `views/matches.py`. The "auto-refresh while a run is active" piece (R-7) is still open.                                                                                                                                                                | `streamlit_app.py`, `views/matches.py`                                                               | empty-state test; smoke                                    |
| 2 **(DONE)**      | Merge Matches: one view, active-track `segmented_control` sort + Companies `st.tabs`; **select-row -> action-button**. Retired the 4 analytics views + `components/tracks.py`.                                                                                                                                             | `views/matches.py` from `analytics.py`; `nav.py`                                                     | matches view + active-track + select-row tests; smoke      |
| 3                 | **(DONE)** Contextual filters: min-score/search/excluded moved out of the global sidebar into the Matches view (the only consumer; Searches never used them). Values persist on the `flt_*` mirror keys.                                                                                                                                                                                                                                   | `streamlit_app.py`, `views/matches.py`                                           | `test_cross_run_filters_are_contextual_to_matches`; smoke                                 |
| 4                 | **(DONE)** Detail screens are click-through (demoted to hidden destinations in Phase 0); Phase 4 added an explicit in-app Back on each via the shared `nav.back_button` helper (labels track `DISPLAY_TITLE`).                                                                                                                                                                          | `nav.py`, `streamlit_app.py`, the detail views                                                       | routing/back tests; smoke                                  |
| 5 (Tier 2)        | Opportunity page: new `views/opportunity.py` merging Job Detail + the **full** per-job action region (deep-review, tailoring drafts + decisions + ADR-072 chat, interview prep, cost/"already-run" badges - ~2x the naive estimate); route every job selection to it.                                                      | new `views/opportunity.py`; `views/matches.py`, `workflow_detail.py`, `job_detail.py`, `components/` | opportunity + routing + no-tracking guardrail tests; smoke |
| 6 (Tier 2)        | Shrink Workflow Detail -> "Search detail" run summary + job list (+ the manual-selection picker variant); Job Detail subsumed.                                                                                                                                                                                             | `views/workflow_detail.py`, `nav.py`                                                                 | detail view test; smoke                                    |

Operator-drawer reordering is folded into Phase 0 (just the rule); it is not its own
phase (UX-review: near-zero user value, pure churn for a single operator).

Rollback: each phase is a separate commit; nav is the single source of truth, so a
revert is one file plus its test.

---

## 6. Test plan

- **`test_ui_structure`** (the nav/registry invariant) updated per phase - this is
  the forcing function that keeps nav and the registry in sync.
- **`smoke-test-ui` skill** after every phase: render all screens headlessly, assert
  no screen raises; add a real-browser screenshot pass for Matches + Opportunity.
- **New unit tests:** Matches segment selection shows only active tracks (ADR-071);
  filter controls render only on Matches/Searches; job-row click routes to the
  Opportunity page with the right `detail_job_id`; Opportunity actions call the
  existing endpoints (deep-review / tailorings / interview-prep).
- **Guardrail test:** assert the Opportunity page exposes no apply/save/status
  control (string-scan, mirroring the spirit of the no-app-tracking rule).

---

## 7. Recommendations (prioritized)

| # | Recommendation | Tier | Effort | Impact |
|---|---|---|---|---|
| 1 | Journey-grouped nav + rename "Workflow*"; demote detail screens to click-through | 1 | S | High |
| 2 | Merge Top Matches + 3 tracks + Companies into one track-aware Matches screen | 1 | M | High |
| 3 | Land on Matches (empty state -> "Start your first search") | 1 | S | High |
| 4 | Contextual sidebar filters (only where they act) | 1 | S | Med |
| 5 | Operator drawer (Settings + Spend & Health under MANAGE; rename) | 1 | S | Med |
| 6 | Job-centric Opportunity page; route all job clicks to it | 2 | L | High |
| 7 | Shrink Workflow Detail to a run summary + job list | 2 | M | Med |

Suggested order: 1 -> 3 -> 4 -> 2 -> 5 (Tier 1), then 6 -> 7 (Tier 2).

---

## 8. Risks & mitigations

- **Nav/registry drift breaking dispatch.** Mitigation: `test_ui_structure` updated
  in the same commit; the registry already covers every nav view.
- **Losing a capability in the merge.** Mitigation: capability checklist mapped
  old-screen -> new-home before deleting any view; smoke test each phase.
- **Operator (you) loses fast access to dashboards.** Open question O-1 below -
  dashboards can stay pinned if preferred.
- **Scope creep into application tracking on the Opportunity page.** Mitigation: the
  ADR-088 section-E guardrail + the section-6 guardrail test.

---

## 9. Open questions for sign-off

- **O-1.** Primary user: optimize purely for the job seeker (operator tools to the
  MANAGE drawer), or keep Spend & Health pinned near the top for your daily
  monitoring? (Affects A/G.)
- **O-2.** Do both tiers in one effort, or ship Tier 1, live with it for a few runs,
  then decide on Tier 2?
- **O-3.** Merge style for the analytics screens: one Matches view with in-page
  segments (proposed), or keep them as deep-links from Matches?
- **O-4.** Naming: "Matches" vs "Opportunities" vs "My roles"; "Searches" vs "Runs";
  "Spend & Health" vs "System".
- **O-5.** Should the first-run path force the onboarding wizard, or just show the
  Matches empty-state CTA?

---

## 10. UX review (recorded 2026-06-07)

An independent UX/product review of ADR-088 + this plan was run before any code.
Verdict: diagnosis correct, direction right, no fatal flaws and nothing that
violates the no-application-tracking rule. Material findings, all folded in above:

- **R-1.** Streamlit has no URL routing / browser-Back integration; the `_navigate`
  rerun hack is already fragile (the `_detail_wf_synced` workaround exists because of
  it). Every click-through destination needs an explicit in-app Back; the browser
  Back will actively mislead. (-> section 2 note, phase 4 last.)
- **R-2.** `st.dataframe` cannot render in-row buttons or in-app links. The proposed
  "Open >" rows are not buildable. Reuse the proven **select-row -> action-button**
  pattern. (-> wireframe 3.2, phase 2.)
- **R-3.** "Companies" is a different layout, not a re-sort - make it an `st.tabs`
  view, not a peer of the track sort segments. (-> 3.2, phase 2.)
- **R-4.** The Opportunity page must absorb the full tailoring drafts + decisions +
  ADR-072 chat region and the cost/"already-run" legibility, or the tailoring flow
  splits across screens (worse than today). ~2x the naive estimate. (-> 3.3, phase 5.)
- **R-5.** Manual-selection (curate-before-score) regresses if the picker is buried
  under Searches; surface "Pick jobs to score" from the Active Run widget. (-> 4.3.)
- **R-6.** Empty state must branch on whether a resume exists. (-> 3.2.)
- **R-7.** "Auto-land on Matches when the run finishes" is not possible (Streamlit
  can't push); say "on next refresh/return" + offer auto-refresh while a run is
  active. (-> 4.1.)
- **IA.** "Searches" belongs under FIND, not MY OPPORTUNITIES; drop the "MANAGE"
  noun (we removed "Workflow" vocabulary - don't add operator-speak). (-> section 2.)
- **Exclude drift.** Keep Exclude de-emphasized + labelled as filtering; never add a
  complementary "pursuing/shortlist/saved" set (back-door application tracking). The
  guardrail test scans for those words too. (-> 3.3, section 6.)
- **Phase order.** Pure rename first; fragile click-through demotion last; merge
  before filters; defer the operator-drawer phase. (-> section 5.)

The most likely thing to get half-built and feel broken is R-2 (the Matches
interaction); the place new users will get stuck is R-7 (async completion).

---

## 11. Should we change the UI framework? (Streamlit vs alternatives)

The UX review surfaced that several frictions are **Streamlit-structural**, not
layout choices: no client-side routing or browser-Back, full-script rerun on every
interaction (tab/segment state resets), no server-push for async run completion, and
no in-row interactive widgets in `st.dataframe`. Because ADR-075 already funnels
every read and write through the FastAPI API, the front end is genuinely swappable -
the backend does not care what renders it. So the question is fair and well-timed.

| Option | What it is | Fixes the Streamlit pain? | Cost / risk |
|---|---|---|---|
| **Stay on Streamlit** (this plan) | Reorg within Streamlit | No - works around R-1/R-2/R-7 | Lowest; days |
| **Streamlit, lean into native multipage + new widgets** | `st.Page`/`st.navigation`, `st.segmented_control`, fragments + `st.rerun(scope)`, partial auto-refresh | Partially - real per-page URLs and scoped reruns reduce R-1/R-7; in-row links/buttons still limited (R-2) | Low; days |
| **Reflex / NiceGUI** (Python, reactive) | Python -> compiled React (Reflex) or Python over a JS runtime (NiceGUI) | Mostly - real routing, components, push, stateful widgets; stays Python | Medium; ~1-2 wks; new framework to learn |
| **React/Next SPA on the FastAPI** | Real front end consuming the API | Fully - routing, Back, components, websockets/polling, rich tables with row actions | High; weeks; a second language/build/test stack |
| **HTMX + server templates** | Hypermedia over FastAPI (Jinja) | Mostly - URLs, partial updates, progressive; lighter than SPA | Medium; rethinks the view layer |

**Recommendation:** do **not** start a framework migration as part of this redesign,
but make the redesign migration-friendly. Rationale:

1. The journey IA (groups, Matches-as-home, one-page-per-job, no app tracking) is
   **framework-independent**. The wireframes and workflows in sections 3-4 are the
   durable design; they transfer to any stack. Locking the IA first is the right
   sequence regardless of framework.
2. ADR-075 already isolates the swap surface, so migrating later is additive, not a
   rewrite of business logic - exactly why ADR-088 keeps all reads/writes on the API.
3. For a single-user family tool, the SPA's strengths (multi-user, SEO, polish at
   scale) are low-value; its costs (a second stack to maintain solo) are high. The
   highest expected-value move is the **second row**: stay on Streamlit but adopt its
   native multipage + scoped-rerun primitives, which retire R-1/R-7 cheaply.
4. If, after living on the reorganized Streamlit UI for a few real runs, the
   remaining friction (mainly rich row-action tables, R-2) still hurts, **Reflex or a
   small React SPA becomes the justified next ADR** - decided with evidence, not now.

**CHOSEN (2026-06-07):** the second row - ship the journey reorg on Streamlit using
native multipage (`st.navigation`/`st.Page`, adopted in Phase 0 so nav groups become
real routable pages, closing R-1) + `segmented_control` + fragments where they
directly retire a friction. Keep the API seam clean, and treat "migrate the front
end" as a separate, evidence-gated ADR decided after living on this.

---

## 12. Capability parity - is any function lost?

No. The reorg is presentation-only; every current capability keeps a home. The
forcing function is a capability checklist mapped old-screen -> new-home, verified by
the per-phase smoke test before any view is deleted.

| Today | New home | Notes |
|---|---|---|
| Workflow History | **Searches** (FIND) | Renamed; same list |
| Workflow Detail (per-run) | **Search detail** + per-job moves to Opportunity | Run-level summary stays; job blocks relocate |
| Job Detail (read-only) | **Opportunity page** | Subsumed; gains the actions |
| Start New Run (+ all opt-ins) | **New search** | Unchanged form, incl. ADR-060/065/079/080 toggles |
| Live Run Monitor (+ Cancel) | Click-through destination | Cancel + cooperative-stop preserved (ADR-083) |
| Run Report (+ download) | Click-through destination | Unchanged |
| Top Matches / IC / Architect / Management | **Matches** (active-track sort) | 4 -> 1; inactive-track dead screens removed (ADR-071) |
| Companies | **Matches -> Companies tab** | Same chart + table |
| Resume Clinic (+ chat + export) | **Resume Clinic** | Unchanged |
| Profiles (+ wizard, resume mgmt) | **Profiles & Resumes** | Renamed; same |
| Settings (search/scoring/agent models/retention) | **Settings** | Unchanged |
| System Dashboard | **Spend & Health** | Renamed; same content |
| Per-job tailoring drafts + decisions + chat | **Opportunity page** | Relocated, not removed (R-4) |
| Manual-selection picker | **Search detail** + Active Run entry | Relocated + made easier (R-5) |
| Exclude job (ADR-057) | **Opportunity page / Matches** | Stays a filter input, not a status |

The only deletions are the four redundant analytics view modules, whose function is
absorbed by Matches. Nothing the user can do today disappears.

---

## 13. Does the new UX make adding functions easier?

Yes, in three concrete ways - and this is a design goal, not a side effect:

1. **One obvious insertion point per concern.** Today a new per-job capability has
   two plausible homes (read it in Job Detail, act on it in Workflow Detail's
   expanders) and usually lands as yet another expander on the 600-line mega-page.
   After the reorg, a new *per-job* feature has exactly one home (the Opportunity
   page), a new *cross-run* lens is a segment/tab on Matches, and a new *operator*
   panel goes under the Settings/Spend rule. Less "where does this go?", less
   mega-page accretion.
2. **The `render(ctx)` + `REGISTRY` + nav-group structure makes a new screen a
   declarative add.** A new view is: add `views/<name>.py` with `render(ctx)`,
   register it, drop it in the right nav group - the same one-file pattern the UI
   refactor established, now with journey groups so it is clear which group it joins.
3. **`test_ui_structure` + the smoke test are the guardrails** that keep additions
   honest: nav and registry can't drift, and every screen must render. Adding a
   function means adding its test alongside - the convention already exists.

Caveat (honest): the Opportunity page becomes the highest-traffic surface, so it
carries the same "don't let it become the new mega-page" risk the current Workflow
Detail has. Mitigation: per-job features that are genuinely independent get their own
collapsed section with a clear heading, and anything cross-job belongs on Matches,
not here. The IA gives a rule for that judgment; the old layout did not.

---

## 14. Is the UI modular, with multiple views?

Yes - and the reorg strengthens both, without changing the underlying module model.

- **It is already modular today.** Each screen is an isolated `app/ui/views/<name>.py`
  exposing `render(ctx)`; `nav.py` is the single source of truth for the view set and
  the `REGISTRY` maps name -> render (the ui_refactor_plan.md structure). The
  entrypoint is a thin shell that only dispatches. Shared rendering lives in
  `components/` (bullets, tailoring card, tracks, resume-chat panel), pure formatters
  in `formatting.py`, cached reads in `data.py`, all backend calls in `api_client.py`.
- **The reorg keeps that model and adds two things:** (a) journey **groups** over the
  flat view list (a grouping layer in `nav.py`, not a new mechanism), and (b) a
  distinction between **nav views** (in the sidebar) and **destination views** (the
  same `render(ctx)` modules, reached by `_navigate` instead of the radio). Both are
  configuration over the existing registry, not a rewrite.
- **Multiple views: yes, and fewer top-level ones.** ~15 nav entries become ~7
  groups/items; the merged Matches and the new Opportunity page are still ordinary
  view modules. So the app stays multi-view and modular - just organized by the user's
  journey instead of by the system's tables.
- **If we later adopt `st.navigation`/`st.Page` (section 11), each view module becomes
  a routable page** with its own URL - more modular still, and closing the routing
  friction (R-1) - while the `render(ctx)` modules barely change.
