"""Start New Run view - kick off a workflow with inline settings + custom URLs.

Phase 3 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted into
render(ctx); the sidebar's min_score now arrives via ctx.min_score. All st.* calls
run inside render().
"""
from __future__ import annotations

import streamlit as st

import app.ui.api_client as api
from app.ui.data import _cached_user_resumes, _get_config_cached
from app.ui.formatting import locations_to_text, parse_locations_input
from app.ui.nav import ViewContext, _navigate


def render(ctx: ViewContext) -> None:
    st.header("Start New Run")

    cfg = _get_config_cached()
    eff = cfg.get("effective_config", {})
    search_cfg = eff.get("search", {}) or {}
    scoring_cfg = eff.get("scoring", {}) or {}

    _default_roles = ", ".join(search_cfg.get("titles", []))
    # ADR-064/BUG-011: locations are one-per-line so "City, State" survives (a comma
    # split would shatter "Atlanta, GA" into "Atlanta" + "GA"). Shared seam with the
    # Settings page via parse_locations_input / locations_to_text.
    _default_locations = locations_to_text(search_cfg.get("locations", []))

    with st.expander("📋 Settings in play for this run", expanded=True):
        st.caption(
            "Defaults below come from your saved settings. Edits here apply to this run only "
            "and are persisted as overrides so future runs reuse them."
        )

    with st.form("start_run"):
        c1, c2 = st.columns(2)
        with c1:
            # ADR-062: pick from this profile's stored resumes instead of typing a
            # raw id. Falls back to a text box if the profile has no resumes yet
            # (e.g. before onboarding step 2) so a run is still possible.
            _resumes = _cached_user_resumes(st.session_state.current_user_id).get("items") or []
            if _resumes:
                _rid_options = [r["resume_id"] for r in _resumes]
                _rid_labels = {
                    r["resume_id"]: (f"{r['file_name'] or r['resume_id']}"
                                     + ("  ·  active" if r["is_active"] else ""))
                    for r in _resumes
                }
                resume_id = st.selectbox(
                    "Resume",
                    _rid_options,
                    format_func=lambda i: _rid_labels.get(i, i),
                    help="This profile's resumes. The active one is listed first. "
                         "Add more via Profiles > Add profile, or Settings.",
                )
            else:
                resume_id = st.text_input(
                    "Resume ID", value="resume.pdf",
                    help="This profile has no stored resume yet. Enter 'resume.pdf' "
                         "to parse a file in the project root, or add one via Profiles.",
                )
            roles = st.text_input(
                "Roles (comma-separated)",
                value=_default_roles or "Staff Engineer, Principal Engineer",
            )
            locations = st.text_area(
                "Locations (one per line)",
                value=_default_locations or "Remote",
                height=90,
                help="One location per line, e.g. 'Atlanta, GA' on its own line. "
                     "Use 'Remote' (its own line) for a US-wide remote search.",
            )
        with c2:
            run_threshold = st.slider(
                "Min match score for this run",
                min_value=0, max_value=100,
                value=int(scoring_cfg.get("min_match_score", ctx.min_score)),
                step=5,
                help="Any track score (tech/arch/lead) at or above this triggers deep review + prep.",
            )
            max_scored = st.number_input(
                "Max jobs to score",
                min_value=1, max_value=25,
                value=int(scoring_cfg.get("max_scored", 10)),
                help="ADR-061: how many jobs get research + scoring (the funnel's "
                     "scored width). Default 10, ceiling 25. In auto mode this is "
                     "also the discovery cap.",
            )
            manual_scoring = st.checkbox(
                "Let me pick which jobs to score (review before scoring)",
                value=bool(scoring_cfg.get("manual_selection", False)),
                help="ADR-060: discover a wider net, then choose which jobs are "
                     "worth the research + scoring spend. Only the jobs you pick "
                     "are scored; the rest are skipped at no cost.",
            )
            max_discovered = st.number_input(
                "Discovery net width (manual mode only)",
                min_value=1, max_value=50,
                value=int(search_cfg.get("max_discovered", 50)),
                help="ADR-061: how many jobs to surface for triage when manual "
                     "selection is on. Default 50, ceiling 50. Ignored in auto mode.",
            )
            # ADR-065: experience window (per profile, off by default).
            min_years_experience = st.number_input(
                "Min years of experience (0 = no limit)",
                min_value=0, max_value=20,
                value=int(search_cfg.get("min_years_experience") or 0),
                help="ADR-065: drop postings asking for fewer years than this "
                     "(e.g. 5 = exclude junior roles). Postings that don't state "
                     "experience are kept. 0 disables the floor.",
            )
            max_years_experience = st.number_input(
                "Max years of experience (0 = no limit)",
                min_value=0, max_value=20,
                value=int(search_cfg.get("max_years_experience") or 0),
                help="ADR-065: drop postings asking for more years than this "
                     "(e.g. 2 = target 0-2 yrs). Postings that don't state "
                     "experience are kept. 0 disables the cap.",
            )
            exclude_senior = st.checkbox(
                "Exclude senior roles",
                value=bool(search_cfg.get("exclude_senior", False)),
                help="ADR-065: drop senior/principal/staff/lead/director/manager/architect "
                     "roles at the source and by title. Use for entry-level profiles.",
            )
            max_posting_age_days = st.number_input(
                "Max posting age in days (0 = no limit)",
                min_value=0, max_value=365,
                value=int(search_cfg.get("max_posting_age_days") or 0),
                help="ADR-080: drop postings older than this many days at discovery "
                     "(before the relevance filter and scoring). Stale postings often "
                     "have dead apply links. Postings with no date are kept. 0 = off.",
            )
            # ADR-095: best-effort dead-link check. Network I/O (adds latency), so opt-in.
            drop_dead_links = st.checkbox(
                "Drop jobs whose apply link is dead",
                value=bool(search_cfg.get("drop_dead_links", False)),
                help="ADR-095: at discovery, check each apply link and drop the ones "
                     "that are verifiably dead (404/410 or a 'no longer available' page) "
                     "so a match never points at a broken link. Conservative - keeps the "
                     "job on any timeout / rate-limit / ambiguous response. Adds some "
                     "latency (one bounded web request per job). Off by default.",
            )
            relevance_filter = st.checkbox(
                "Reasoning relevance filter (drop mismatches before scoring)",
                value=bool(search_cfg.get("relevance_filter", False)),
                help="ADR-079: one cheap LLM pass reasons over every discovered job "
                     "and drops clear seniority or role mismatches BEFORE scoring, so "
                     "you don't pay to score the noise. Judged against your own level, "
                     "so it drops too-senior roles for an early-career profile and "
                     "too-junior roles for a senior one. Widens discovery to triage from.",
            )
            # ADR-094: clearance exclusion rides the relevance filter. Deterministic
            # (keyword), so it adds no LLM cost; only acts when the relevance filter
            # above is enabled. Default off so cleared candidates keep cleared roles.
            exclude_clearance = st.checkbox(
                "↳ also exclude jobs requiring a security clearance",
                value=bool(search_cfg.get("exclude_clearance", False)),
                help="ADR-094: when the relevance filter is on, also drop postings that "
                     "require a US/government security clearance (TS/SCI, Secret, "
                     "polygraph, DoD clearance, ...). Deterministic keyword match - no "
                     "extra LLM cost. Has no effect unless the relevance filter is on.",
            )
            persist_prefs = st.checkbox(
                "Save these settings as my defaults for future runs",
                value=False,
            )

        st.markdown("**Custom job URLs** (optional, one per line — LinkedIn, company career pages, etc.)")
        custom_urls_raw = st.text_area(
            "URLs",
            value="",
            height=120,
            label_visibility="collapsed",
            placeholder="https://www.linkedin.com/jobs/view/123\nhttps://acme.com/careers/staff-engineer",
        )

        # The button greys out while a submission is in flight (the guard below).
        _submitting = st.session_state.get("_run_submitting", False)
        submitted = st.form_submit_button(
            "Submitting…" if _submitting else "Start Workflow",
            disabled=_submitting,
        )
        # Persist the form's search settings to THIS profile without starting a run
        # (the persist checkbox above only fires when a run is also started).
        save_only = st.form_submit_button(
            "Save settings to my profile",
            disabled=_submitting,
            help="Save these roles, locations, and filters as your profile's "
                 "defaults for future runs. Does not start a workflow.",
        )

    # Two-phase submit. Each kickoff gets a fresh Idempotency-Key (api_client), so
    # the server can't dedupe two distinct user clicks - a double-click would start
    # two runs. So the guard lives here: phase A (this click) captures the form
    # payload, raises _run_submitting, and reruns so the button re-renders disabled;
    # phase B (the rerun) executes the stashed payload while the button is greyed,
    # then navigates away (success) or clears the guard so the user can retry (error).
    if (submitted or save_only) and not _submitting:
        custom_urls = [u.strip() for u in custom_urls_raw.splitlines() if u.strip()]

        search_criteria = {
            "roles": [r.strip() for r in roles.split(",") if r.strip()],
            # BUG-011/ADR-064: one-per-line so "Atlanta, GA" survives (shared seam).
            "locations": parse_locations_input(locations),
        }
        # ADR-071: the run inherits the profile's active scoring tracks. Validate
        # against the three known names; an empty/invalid set is omitted so the
        # backend's all-three default applies (Primary unchanged).
        _valid_tracks = ("ic", "architect", "management")
        _profile_tracks = scoring_cfg.get("tracks")
        run_tracks = (
            [t for t in _valid_tracks if t in _profile_tracks]
            if isinstance(_profile_tracks, list) else []
        )
        effective_config = {
            "scoring": {
                "career_track": "all",
                "min_match_score": int(run_threshold),
                "manual_selection": bool(manual_scoring),
                "max_scored": int(max_scored),
                **({"tracks": run_tracks} if run_tracks else {}),
            },
            "search": {
                "max_discovered": int(max_discovered),
                # ADR-065: 0 = no limit (omit so discovery leaves that bound off).
                **({"max_years_experience": int(max_years_experience)}
                   if int(max_years_experience) > 0 else {}),
                **({"min_years_experience": int(min_years_experience)}
                   if int(min_years_experience) > 0 else {}),
                "exclude_senior": bool(exclude_senior),
                # ADR-079: opt-in reasoning pre-filter before scoring.
                "relevance_filter": bool(relevance_filter),
                # ADR-094: clearance exclusion within the relevance filter (opt-in).
                "exclude_clearance": bool(exclude_clearance),
                # ADR-080: 0 = off (omit so discovery leaves the age bound off).
                **({"max_posting_age_days": int(max_posting_age_days)}
                   if int(max_posting_age_days) > 0 else {}),
                # ADR-095: best-effort dead-link drop at discovery (opt-in).
                "drop_dead_links": bool(drop_dead_links),
            },
        }

        # The profile defaults this form persists -- shared by the save-only path
        # (below) and the save-with-run path (phase B). Widget values are only
        # readable on this click run, so capture them now.
        prefs = {
            "scoring.min_match_score": int(run_threshold),
            "scoring.manual_selection": bool(manual_scoring),
            "scoring.max_scored": int(max_scored),
            "search.max_discovered": int(max_discovered),
            "search.titles": search_criteria["roles"],
            "search.locations": search_criteria["locations"],
            "search.max_years_experience": int(max_years_experience),
            "search.min_years_experience": int(min_years_experience),
            "search.exclude_senior": bool(exclude_senior),
            "search.relevance_filter": bool(relevance_filter),
            "search.exclude_clearance": bool(exclude_clearance),
            "search.max_posting_age_days": int(max_posting_age_days),
            "search.drop_dead_links": bool(drop_dead_links),
        }
        # Save-only: persist these settings to the acting profile and stop -- no run.
        # (put_config carries the profile via the ?user_id= seam, so this writes to
        # THIS profile; invalidate the config cache so the form re-reads the saved
        # values on the next render.)
        if save_only:
            try:
                for _key, _val in prefs.items():
                    api.put_config(_key, _val)
                st.session_state.config_cache = None
                st.success("Saved these search settings as defaults for your profile.")
            except Exception as exc:
                st.error(f"Save failed: {exc}")
            return

        # Stash everything needed by phase B. The form's widget values are only
        # available on this click run; a plain st.rerun would lose them, so capture
        # the built payload (and any prefs to persist) into session_state now.
        st.session_state._pending_run = {
            "resume_id": resume_id,
            "search_criteria": search_criteria,
            "effective_config": effective_config,
            "custom_urls": custom_urls,
            "prefs": prefs if persist_prefs else None,
        }
        st.session_state._run_submitting = True
        st.rerun()  # re-render with the button disabled, then run phase B below

    # Phase B: a submission is in flight - execute it with the button greyed out.
    if st.session_state.get("_run_submitting") and st.session_state.get("_pending_run"):
        pending = st.session_state._pending_run

        if pending.get("prefs"):
            try:
                for _key, _val in pending["prefs"].items():
                    api.put_config(_key, _val)
                st.session_state.config_cache = None  # invalidate
            except Exception as exc:
                st.warning(f"Settings save failed (run will still start): {exc}")

        try:
            with st.spinner("Submitting workflow…"):
                resp = api.start_workflow(
                    pending["resume_id"], pending["search_criteria"],
                    effective_config=pending["effective_config"],
                    custom_urls=pending["custom_urls"],
                )
            st.session_state.workflow_id = resp["workflow_id"]
            st.session_state.last_status = "running"
            st.session_state.last_response = resp
            st.session_state.detail_workflow_id = resp["workflow_id"]
            # Clear the guard before leaving so returning to this page is clean.
            st.session_state._run_submitting = False
            st.session_state._pending_run = None
            # Take the user straight to the Live monitor once the workflow_id is
            # available, so they can watch the run unfold. The Live monitor
            # auto-refreshes while the run is active (no manual Refresh needed);
            # on completion it hands off to the run's detail page. (Matches still
            # hosts the ADR-089 status strip for anyone who navigates there.)
            st.toast("Search started — taking you to the live run.")
            _navigate("Live Run Monitor")
        except Exception as exc:
            # Failed to start: drop the guard so the button re-enables for a retry.
            st.session_state._run_submitting = False
            st.session_state._pending_run = None
            st.error(f"Failed to start workflow: {exc}")
