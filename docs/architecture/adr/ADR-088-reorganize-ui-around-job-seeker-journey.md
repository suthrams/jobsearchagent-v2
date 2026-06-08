# ADR-088: Reorganize the UI Around the Job-Seeker Journey

## Status

Accepted (2026-06-07). Mockups signed off; implementation in progress (phases 0-6,
see `ui_journey_reorg_plan.md`). **Shipped so far:** Phase 2 (merged Matches),
Phase 0 (native multipage `st.navigation`/`st.Page` + journey groups + the
Workflow*->user-word rename + hidden click-through destinations + land on Matches),
Phase 3 (cross-run filters moved out of the global sidebar into the Matches view
that consumes them), Phase 4 (explicit in-app Back on every hidden destination via
`nav.back_button`), and Phase 5 (the Tier-2 Opportunity page: one per-job surface
merging the read-only Job Detail with the full per-job action region — deep review /
tailoring drafts+decisions+ADR-072 chat / interview prep on demand + exclude — routed
to from every job click; the no-app-tracking guardrail holds, enforced by a test).
Open: Phase 6 (shrink Workflow Detail to a run summary).

**Decisions locked (2026-06-07, owner):**
- **Framework:** stay on Streamlit but adopt its **native multipage**
  (`st.navigation` / `st.Page`) + fragments / scoped reruns - the journey groups
  become real routable pages (closes UX-review R-1 / R-7 routing + async-refresh
  friction) without leaving Python. A move to a different stack stays a separate,
  evidence-gated ADR (plan section 11).
- **Scope:** implement **both tiers** (phases 0-6) in this effort.
- **Primary user:** optimize for the **job seeker** - operator screens (Settings,
  Spend & Health) sit below a sidebar rule, out of the first glance.
- **Mockups:** polished rendered mockups reviewed and signed off before code.

Builds on ADR-075 (UI reads through the API), the ADR-062 profile model, ADR-071
(per-profile active tracks), and the ui_refactor_plan.md `render(ctx)` + `REGISTRY`
structure. Implementation detail, wireframes, and user flows live in the companion
[`ui_journey_reorg_plan.md`](../ui_journey_reorg_plan.md).

## Context

The Streamlit UI works but its information architecture mirrors the *system's
architecture*, not the *user's journey*. A critical review of the live IA
(`app/ui/nav.py:32-49`, `streamlit_app.py`, the 12 view modules) found:

1. **System vocabulary, not user vocabulary.** The sidebar leads with "Workflow
   History" and "Workflow Detail"; "Workflow" appears three times. A job seeker
   thinks "search / my matches / this role", never "workflow".
2. **Detail screens are nav items, so they dead-end.** "Workflow Detail" and "Job
   Detail" are top-level radio entries (`nav.py:34-35`) that show "pick a run" when
   nothing is selected (`job_detail.py:28`, `workflow_detail.py:164`). They are
   destinations reached by a click, not navigation.
3. **Five analytics screens are one table re-sorted.** Top Matches, IC Track,
   Architect Track, Management Track, Companies (`nav.py:44-48`). For a profile
   that pursues one or two tracks (the ADR-071 norm), the inactive track screens
   render only a "not active for this profile" notice (`analytics.py:44`).
4. **The payoff is buried.** The user's deliverable - "which roles should I pursue
   and how do I land them" - lives below the analytics separator, and per-job
   actions (tailor, interview prep, clinic) are the 4th-5th subheader inside the
   Workflow Detail mega-page (`workflow_detail.py:425,483`, two sections both
   titled "Prep"). It is 3+ clicks and a long scroll deep.
5. **Run-centric framing fights the user's model.** Users care about *jobs*, not
   which run surfaced them, yet acting on a job requires remembering its run and
   drilling History -> Detail -> the job's expander. "Job Detail" (read-only) and
   Workflow Detail's per-job expanders (the actions) are two half-views of one job.
6. **Two audiences are interleaved.** Operator/builder screens (System Dashboard,
   Settings agent-models/retention, Live Monitor, Diagnostics) sit shoulder-to-
   shoulder with the job-seeker screens and dominate the chrome.
7. **Global filters that are mostly inert.** The min-score slider, search box, and
   include-excluded checkbox render on every screen (`streamlit_app.py:162-175`)
   but act only on the browse/analytics views.

What already works and must be preserved: the profile selector, the "Active Run"
status widget, the one-page-per-run instinct, the `render(ctx)` + `REGISTRY`
dispatch, and the ADR-075 API-only read funnel.

## Decision

Reorganize the UI around the job-seeker journey in two tiers, keeping every
existing capability and the existing view-dispatch architecture.

### A. Journey-centric navigation model (Tier 1)

Replace the flat 15-item radio + single separator with journey groups, ordered by
what the user is doing, and demote detail screens out of the nav:

```
FIND            -> New search                 (was Start New Run)
                   Searches                    (was Workflow History; the run list)
MY OPPORTUNITIES-> Matches                     (merges Top Matches + 3 track views;
                                                Companies is an in-page tab)
RESUME          -> Resume Clinic
                   Profiles & Resumes          (was Profiles)
----------------- (a rule, no operator noun)
                   Settings
                   Spend & Health              (was System Dashboard)
```

(Grouping revised after the UX review - see `ui_journey_reorg_plan.md` section 11:
Searches sits under FIND, not MY OPPORTUNITIES; the operator screens get a plain
rule, not a "MANAGE" noun, since the whole point is to drop system vocabulary.)

Net ~15 nav entries -> ~7. "Workflow Detail", "Job Detail", "Run Report", and
"Live Run Monitor" stop being nav radio entries and become **click-through
destinations** with a Back affordance (see F). The grouping is rendered with
Streamlit section captions/headers in the sidebar; no new framework.

### B. Merged Matches screen (Tier 1)

Collapse Top Matches + IC/Architect/Management + Companies into one **Matches**
view. Track sort uses a `segmented_control` showing only the profile's **active**
tracks (`active_track_keys`, ADR-071), so inactive-track dead screens disappear.
Companies is a separate `st.tabs` view (a different layout, not a re-sort - UX
review R-3). Row interaction is **select-row -> action-button** (reusing the proven
`workflow_detail.py` pattern), because `st.dataframe` cannot render in-row links or
buttons (UX review R-2). This is the job-seeker's home base.

### C. Contextual sidebar filters (Tier 1)

The min-score / search / include-excluded controls render only on the screens
where they act (Matches, Searches). On New search / Settings / Profiles / Clinic
they are hidden. Filter state moves from always-on sidebar widgets into the
`ViewContext` only where consumed.

### D. Journey landing + empty states (Tier 1)

The default landing becomes **Matches** (the payoff), not Workflow History. With
no runs yet, Matches shows a first-run empty state with a single primary CTA -
"Start your first search" -> New search. After a run completes, the user lands on
Matches with the latest run's new matches surfaced.

### E. Job-centric Opportunity page (Tier 2 - the bigger bet)

A single page per job that opens on any job selection from anywhere (Matches, a
Search, the Active Run). It merges today's read-only Job Detail with Workflow
Detail's per-job action region into one surface: fit summary, resume-gap vs
career-gap, deep review (run on demand if absent), and the primary actions
**Tailor my resume** and **Prep for interview**, plus **Exclude** (the ADR-057
filter input). Scope note (UX review R-4): "the per-job action region" means the
**full** tailoring flow - drafts list, approve/revise/reject/edit decisions, and
the ADR-072 live-chat panel - plus the on-demand cost / "already-run" legibility
(including the "tailoring a non-selected job runs deep review first" warning).
Anything less splits the tailoring flow across screens, which is worse than today.
The user never has to think in "runs" to act on a job.

**Guardrail (hard project rule).** The Opportunity page must NOT introduce
Apply / Save / application-status fields. "Pursue" here means *preparation*
(tailor, interview prep) and *filtering* (exclude), never outcome tracking. The
subtle drift the UX review flagged: do not let **Exclude** become a status toggle -
keep it de-emphasized and labelled as filtering ("hide from future searches"), and
**never** add a complementary "pursuing / shortlist / saved" set, which would be
application tracking through the back door. The career decision point stays
human-owned (CLAUDE.md "No application tracking";
[[feedback_filter_vs_tracker_distinction]]). The section-6 guardrail test scans for
`apply/save/status` *and* `pursuing/shortlist/saved`.

### F. Detail screens become destinations (Tier 1 -> Tier 2)

Workflow Detail, Job Detail (subsumed by the Opportunity page in Tier 2), Run
Report, and Live Run Monitor are reached by clicking a row/button and render a
Back control to their origin. They are removed from the nav radio but remain in
the `REGISTRY` and reachable via `_navigate(...)` (the mechanism already exists,
`nav.py:69`). In Tier 2, Workflow Detail shrinks to a run summary + "jump to job".

### G. Operator drawer (Tier 1)

Settings and Spend & Health (renamed from System Dashboard) move under a visually
separated MANAGE group. They stay fully available; they just stop competing with
the search journey for the user's first glance.

### H. Scope boundaries / non-goals

- **No backend change for Tier 1.** Reads still go through the API (ADR-075); no
  new endpoints. Tier 2's Opportunity page reuses existing per-job read +
  on-demand action endpoints (deep-review / tailorings / interview-prep).
- **No identity/auth change** (ADR-062 cooperative isolation stands).
- **No application tracking** (section E guardrail).
- **No new agent or workflow node** - this is purely a presentation/IA change.

## Options considered

- **Keep the current IA.** Rejected: it optimizes for the builder, not the job
  seeker the tool is for; the review documented seven concrete frictions.
- **Rename-only (cheapest).** Relabel "Workflow*" to user words but keep the flat
  list. Rejected as insufficient: it fixes vocabulary but not the dead-end detail
  items, the five-way analytics redundancy, or the buried payoff.
- **Top tab-bar instead of a sidebar.** Rejected: Streamlit's native multipage /
  tab ergonomics are weaker than the existing sidebar radio, and it would discard
  the working profile selector + Active Run widget placement.
- **Change the UI framework as part of this work** (React/Next SPA, or Python
  reactive frameworks Reflex / NiceGUI, or HTMX). Deferred to a separate,
  evidence-gated ADR. The ADR-075 API seam makes the front end swappable, and some
  frictions are Streamlit-structural (no routing/Back, full reruns, no push, no
  in-row widgets). But the journey IA in this ADR is framework-independent, so the
  right sequence is: lock the IA, ship it on Streamlit (leaning on native multipage
  + scoped reruns where they directly retire a friction), then decide on migration
  with real usage evidence. Full evaluation matrix + recommendation in
  `ui_journey_reorg_plan.md` section 12.
- **Two-tier reorg inside Streamlit (chosen).** Maximizes UX gain per unit of risk:
  Tier 1 is mostly `nav.py` + view consolidation behind the existing dispatch;
  Tier 2 adds one new page and reuses existing endpoints.

## Consequences

### Positive

- The IA matches the user's journey; the payoff (Matches, then act on a job) is the
  first thing seen and one click from action.
- ~15 nav entries -> ~7; the five redundant analytics screens collapse to one
  track-aware Matches view; inactive-track dead screens disappear (ADR-071).
- Operator and job-seeker concerns are visually separated without losing either.
- No backend, identity, agent, or workflow change; ADR-075 / ADR-062 invariants hold.

### Tradeoffs

- `test_ui_structure` and any nav-count assertions must be updated in lockstep (a
  forcing function, not a surprise).
- Muscle memory churn for the current (single) operator - mitigated by keeping every
  screen reachable and the Active Run widget unchanged.
- Tier 2 adds a new view module and a routing pass over every job-row click; more
  surface to test than Tier 1.

### Neutral

- Docs: this ADR + index; `ui_journey_reorg_plan.md` (wireframes, flows, phases);
  `ui_architecture.md` (nav model + the new Matches/Opportunity screens);
  `user_guide.md` (navigation + journey sections); `wiki.md` if the screen list is
  referenced; CLAUDE.md UI note if nav rules are codified.
- Tests: update `test_ui_structure` for the new nav set; extend the smoke test to
  the merged Matches + Opportunity views; add routing tests for click-through.

## References

- ADR-075 - UI reads through the API (the seam this builds on; no DB access added).
- ADR-062 - Multi-user profiles (the profile selector + cooperative isolation kept).
- ADR-071 - Per-profile active scoring tracks (drives the Matches segment set).
- ADR-057 / ADR-059 - Per-job exclusion (a filter input, in scope) / HITL retired
  (no `interrupt()`; the Opportunity page actions stay out-of-graph and on demand).
- CLAUDE.md "No application tracking" + [[feedback_filter_vs_tracker_distinction]] -
  the section-E guardrail.
- `docs/architecture/ui_refactor_plan.md`, `ui_read_funnel_implementation_plan.md` -
  the `render(ctx)` + `REGISTRY` structure this works within.
