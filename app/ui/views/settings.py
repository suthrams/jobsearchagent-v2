"""Settings view - view/edit user-overridable config, per-agent model picker, and
the ADR-070 data-retention purge control.

Phase 4 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted
verbatim into render(ctx); all st.* calls run inside render().
"""
from __future__ import annotations

import streamlit as st

import app.ui.api_client as api
from app.ui.data import _cached_get_providers, _get_config_cached
from app.ui.formatting import _get_nested, _label_with_cost
from app.ui.nav import ViewContext


def render(ctx: ViewContext) -> None:
    st.header("Settings")

    cfg = _get_config_cached()
    eff = cfg.get("effective_config", {}) or {}
    protected = set(cfg.get("protected_keys", []) or [])
    if cfg.get("_offline_reason"):
        st.warning(f"Backend not reachable — read-only fallback. ({cfg['_offline_reason']})")

    st.caption(
        "Edit the values below to update your defaults. Protected keys (LLM models, "
        "execution limits, retention windows) are read-only and live in `config/config.yaml`."
    )

    def _save(key: str, value: object) -> None:
        """Persist via PUT /config, then POST /config/reload so the backend
        picks up the change without a manual restart (ADR-053 addendum)."""
        try:
            api.put_config(key, value)
            st.session_state.config_cache = None
        except Exception as exc:
            st.error(f"Save failed for `{key}`: {exc}")
            return
        # Reload the backend so the change is live for the next workflow run.
        # Per-agent assignment changes especially need this — ModelRegistry
        # caches one provider per (provider, model) at startup.
        try:
            reload_result = api.reload_config()
            if key.startswith("agents."):
                # Surface the new effective assignment so the user can confirm
                # the agent now points at the chosen model.
                assignment = (reload_result or {}).get("agent_assignment") or {}
                # Extract the agent_name from "agents.{name}..." for the toast.
                parts = key.split(".")
                if len(parts) >= 2:
                    a = parts[1]
                    if a in assignment:
                        m = assignment[a]
                        st.success(
                            f"Saved `{key}` and applied. "
                            f"Active: **{a}** → `{m['provider']}/{m['model']}`"
                        )
                        return
            st.success(f"Saved `{key}` and applied (no restart needed).")
        except Exception as exc:
            st.warning(
                f"Saved `{key}` but the live reload failed: {exc}. "
                "Restart `uvicorn` to apply the change."
            )

    # ── Search ─────────────────────────────────────────────────────────────
    st.subheader("Search")
    search = (eff.get("search") or {}).copy()

    titles_str = st.text_area(
        "search.titles (comma-separated)",
        value=", ".join(search.get("titles", [])),
        height=80,
    )
    if st.button("Save titles"):
        _save("search.titles",
              [t.strip() for t in titles_str.split(",") if t.strip()])

    locations_str = st.text_area(
        "search.locations (comma-separated)",
        value=", ".join(search.get("locations", [])),
        height=60,
    )
    if st.button("Save locations"):
        _save("search.locations",
              [l.strip() for l in locations_str.split(",") if l.strip()])

    max_discovered = st.number_input(
        "search.max_discovered (manual-mode discovery net width)",
        min_value=1, max_value=50,
        value=int(search.get("max_discovered", 50)),
        help="ADR-061: how many jobs to surface for triage when manual selection "
             "is on. Default 50, ceiling 50. Ignored in auto mode.",
    )
    if st.button("Save max_discovered"):
        _save("search.max_discovered", int(max_discovered))

    # ── Scoring ────────────────────────────────────────────────────────────
    st.subheader("Scoring")
    scoring = (eff.get("scoring") or {}).copy()

    # ADR-071: per-profile active scoring tracks. Default all three (Primary).
    _TRACK_LABELS = {
        "ic": "IC (technical)",
        "architect": "Architect (architecture)",
        "management": "Management (leadership)",
    }
    _all_tracks = ["ic", "architect", "management"]
    _current_tracks = scoring.get("tracks")
    if not isinstance(_current_tracks, list) or not _current_tracks:
        _current_tracks = _all_tracks
    chosen_tracks = st.multiselect(
        "scoring.tracks (which career tracks this profile is scored on)",
        options=_all_tracks,
        default=[t for t in _all_tracks if t in _current_tracks],
        format_func=lambda t: _TRACK_LABELS.get(t, t),
        help="ADR-071: most profiles fit 1-2 tracks, not all 3. Inactive tracks "
             "are not scored, do not trigger deep review, and are hidden in the "
             "results. Leave all three selected to score like the Primary profile.",
    )
    if st.button("Save tracks"):
        # Persist in canonical order; empty selection means 'all three' (the
        # backend treats absent/empty as all, but store the explicit full set so
        # the saved value is unambiguous).
        ordered = [t for t in _all_tracks if t in chosen_tracks] or _all_tracks
        _save("scoring.tracks", ordered)

    threshold = st.slider(
        "scoring.min_match_score (any track ≥ this triggers deep review)",
        min_value=0, max_value=100,
        value=int(scoring.get("min_match_score", 75)),
        step=5,
    )
    if st.button("Save min_match_score"):
        _save("scoring.min_match_score", int(threshold))

    max_scored = st.number_input(
        "scoring.max_scored (how many jobs get research + scoring)",
        min_value=1, max_value=25,
        value=int(scoring.get("max_scored", 10)),
        help="ADR-061: the funnel's scored width. Default 10, ceiling 25. In auto "
             "mode this is also the discovery cap; runs can override it.",
    )
    if st.button("Save max_scored"):
        _save("scoring.max_scored", int(max_scored))

    manual_selection_default = st.checkbox(
        "scoring.manual_selection (review discovered jobs before paying to score them)",
        value=bool(scoring.get("manual_selection", False)),
        help="ADR-060: when on, discovery casts a wider net and runs park at a "
             "selection screen so you choose which jobs are worth the research + "
             "scoring spend. This sets the default; each run can still override it "
             "on the Start New Run form.",
    )
    if st.button("Save manual_selection"):
        _save("scoring.manual_selection", bool(manual_selection_default))

    # ── Target companies (ADR-098: per-profile ATS-direct boards) ───────────
    st.markdown("---")
    st.subheader("Target companies (ATS-direct)")
    st.caption(
        "Greenhouse / Lever boards this profile pulls jobs from, in addition to "
        "Adzuna. The list is per-profile and applies on your next run (no restart). "
        "Adding a board verifies it live first, so a dead slug never enters your "
        "list. Saving a list **replaces** this profile's list for that ATS."
    )

    def _save_scrapers(key: str, value: object) -> None:
        """Persist an ATS list/flag override. Unlike _save, this does NOT call
        /config/reload: the company list is resolved per run from effective_config
        (ADR-098), so the next run picks it up with no backend rebuild."""
        try:
            api.put_config(key, value)
            st.session_state.config_cache = None
        except Exception as exc:
            st.error(f"Save failed for `{key}`: {exc}")
            return
        st.success(f"Saved `{key}`. Applies on your next run.")

    scrapers_cfg = (eff.get("scrapers") or {})
    for ats, label in (("greenhouse", "Greenhouse"), ("lever", "Lever")):
        ats_cfg = (scrapers_cfg.get(ats) or {})
        companies = list(ats_cfg.get("companies") or [])
        enabled = bool(ats_cfg.get("enabled", True))
        with st.expander(f"{label}  ·  {len(companies)} board(s)"
                         + ("" if enabled else "  ·  disabled"), expanded=False):
            new_enabled = st.checkbox(
                f"Enable {label} sourcing for this profile",
                value=enabled, key=f"ats_enabled_{ats}",
            )
            if new_enabled != enabled:
                _save_scrapers(f"scrapers.{ats}.enabled", bool(new_enabled))

            if companies:
                st.write(", ".join(f"`{c}`" for c in companies))
            else:
                st.caption("No boards yet — add one below.")

            # Add a board, verified live before it joins the list.
            new_slug = st.text_input(
                f"Add a {label} board token/slug",
                key=f"ats_add_{ats}",
                placeholder="e.g. stripe",
            )
            if st.button(f"Verify & add to {label}", key=f"ats_addbtn_{ats}"):
                slug = (new_slug or "").strip()
                if not slug:
                    st.warning("Enter a board token/slug first.")
                elif slug in companies:
                    st.info(f"`{slug}` is already in your {label} list.")
                else:
                    try:
                        with st.spinner(f"Checking {label} board `{slug}`..."):
                            result = api.verify_ats_board(ats, slug)
                    except Exception as exc:
                        st.error(f"Verify failed: {exc}")
                    else:
                        if result.get("ok"):
                            _save_scrapers(f"scrapers.{ats}.companies",
                                           companies + [slug])
                            st.success(
                                f"Added `{slug}` ({result.get('job_count', 0)} open jobs)."
                            )
                        else:
                            st.error(result.get("message", f"`{slug}` is not a live "
                                                           f"{label} board."))

            # Remove boards.
            if companies:
                to_remove = st.multiselect(
                    "Remove boards", options=companies, key=f"ats_rm_{ats}",
                )
                if st.button(f"Remove from {label}", key=f"ats_rmbtn_{ats}",
                             disabled=not to_remove):
                    remaining = [c for c in companies if c not in set(to_remove)]
                    _save_scrapers(f"scrapers.{ats}.companies", remaining)

    # ── Agent Models (per ADR-053) ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Agent Models")
    st.caption(
        "Pick a provider and model per agent. Indicative cost shown per million tokens. "
        "Saves trigger a live reload of the backend's agent bindings — no manual "
        "restart needed for runtime overrides. In-flight workflows keep their "
        "original assignment; only NEW workflows pick up the change."
    )

    with st.spinner("Loading provider catalog…"):
        providers_payload = _cached_get_providers()
    if providers_payload is None:
        st.warning("Couldn't reach `/config/providers` (backend may be down or restarting).")

    if providers_payload:
        catalog = providers_payload.get("providers", {}) or {}
        agent_assignment = providers_payload.get("agent_assignment", {}) or {}
        meta = catalog.get("_meta", {}) or {}
        high_volume_agents = set(meta.get("high_volume_agents") or [])
        high_volume_safe_models = set(meta.get("high_volume_safe_models") or [])

        if not catalog.get("openai", {}).get("available", False):
            st.info(
                "OpenAI provider is not registered (no `OPENAI_API_KEY` in `.env`). "
                "Add the key and restart the backend to enable OpenAI models."
            )

        # One row per agent; provider dropdown then a model dropdown filtered by it.
        # Iterate only over real agent names; the catalog's "_meta" key is sidecar metadata.
        for agent_name in sorted(a for a in agent_assignment.keys() if not a.startswith("_")):
            assignment = agent_assignment[agent_name]
            current_provider = assignment.get("provider", "claude")
            current_model = assignment.get("model", "")
            cost_capped = agent_name in high_volume_agents

            with st.expander(
                f"`{agent_name}`  ·  current: **{current_provider}** / `{current_model}`"
                + ("  ·  💰 cost-capped" if cost_capped else ""),
                expanded=False,
            ):
                if cost_capped:
                    st.caption(
                        "**Cost-capped agent.** This agent runs on every job (10-20 "
                        "calls per workflow), so its model is restricted to the "
                        f"cheapest tier: `{', '.join(sorted(high_volume_safe_models))}`. "
                        "Cost here is a design decision; expensive models are "
                        "reserved for low-volume, user-facing agents."
                    )

                # Provider options — only show those the server reports as available.
                # For cost-capped agents, also restrict to providers that have at
                # least one allowed model.
                def _has_allowed_model(provider_id: str) -> bool:
                    if not cost_capped:
                        return True
                    return any(
                        m["id"] in high_volume_safe_models
                        for m in (catalog.get(provider_id, {}).get("models") or [])
                    )

                provider_options = [
                    p for p, info in catalog.items()
                    if not p.startswith("_")
                    and (info.get("available", False) or p == current_provider)
                    and _has_allowed_model(p)
                ]
                provider_choice = st.selectbox(
                    "Provider",
                    options=provider_options,
                    index=provider_options.index(current_provider) if current_provider in provider_options else 0,
                    key=f"prov_{agent_name}",
                )

                # Model options for the chosen provider, filtered by cost cap.
                model_entries = catalog.get(provider_choice, {}).get("models", []) or []
                if cost_capped:
                    model_entries = [m for m in model_entries if m["id"] in high_volume_safe_models]
                model_ids = [m["id"] for m in model_entries]
                model_idx = model_ids.index(current_model) if current_model in model_ids else 0
                model_choice = st.selectbox(
                    "Model",
                    options=model_ids,
                    index=model_idx if model_ids else 0,
                    format_func=lambda mid: _label_with_cost(mid, model_entries),
                    key=f"model_{agent_name}",
                )

                if st.button("Save", key=f"save_{agent_name}"):
                    try:
                        api.put_config(f"agents.{agent_name}.provider", provider_choice)
                        api.put_config(f"agents.{agent_name}.model", model_choice)
                        st.session_state.config_cache = None
                        _cached_get_providers.clear()
                        st.success(
                            f"Saved {agent_name} → {provider_choice}/{model_choice}. "
                            "Restart the backend for it to take effect."
                        )
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")

    # ── Read-only protected ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Read-only (protected)")
    st.caption("These cannot be changed via the UI to prevent accidental cost spikes or instability.")
    st.json({k: _get_nested(eff, k.split(".")) for k in sorted(protected)
             if _get_nested(eff, k.split(".")) is not None}, expanded=False)

    # ── Data retention purge (ADR-070) ───────────────────────────────────────
    st.subheader("Data retention")
    st.caption(
        "Delete data past the retention windows above. A purged workflow run takes "
        "ALL its rows with it (scores, reviews, advice, prep, tailorings, clinic "
        "reviews, decisions, observability); inactive resumes are removed only once "
        "no surviving run still references them. The windows are read-only and live "
        "in `config/config.yaml`. Purge is explicit and never runs automatically."
    )
    with st.expander("Run data-retention purge"):
        st.warning(
            "This permanently deletes rows older than the retention windows. It "
            "cannot be undone. Make sure you have a backup of `data/v2.db` if you "
            "might want this data back."
        )
        confirm = st.checkbox(
            "I understand this permanently deletes data past the retention windows.",
            key="purge_confirm",
        )
        if st.button("Run purge now", type="primary", disabled=not confirm,
                     key="purge_run_btn"):
            try:
                with st.spinner("Purging..."):
                    result = api.purge_data()
            except Exception as exc:
                st.error(f"Purge failed: {exc}")
            else:
                deleted = {t: n for t, n in (result or {}).items() if n}
                total = sum(deleted.values())
                if total:
                    st.success(f"Purged {total} rows.")
                    st.json(deleted, expanded=True)
                else:
                    st.info("Nothing was past the retention windows; no rows deleted.")
                # The history/cost views read from the DB directly — invalidate any
                # cached config so a re-render reflects the smaller dataset.
                st.session_state.config_cache = None
