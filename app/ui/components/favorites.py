"""Favorite-job toggle (ADR-090) - shared by Matches and Opportunity so the star
behaves identically on both.

A favorite is a FILTER-INPUT (a job the user flags to tailor toward), the positive
counterpart of the ADR-057 exclude filter - NOT application tracking. This component
only ever favorites / un-favorites; it never records apply/applied/status/outcome.
"""
from __future__ import annotations

import streamlit as st

import app.ui.api_client as api
from app.ui.data import _cached_favorites

# Display copy only; mirrors MAX_FAVORITES in favorite_repository.py.
_CAP = 25


def favorited_ids(user_id: str | None) -> set[str]:
    """The job_ids the active profile has favorited (for badging a list of rows)."""
    return {f.get("job_id") for f in (_cached_favorites(user_id) or []) if f.get("job_id")}


def render_analyze_in_clinic_button(*, job_id: str, workflow_id: str, key: str,
                                    label: str = "🩺 Analyze in clinic",
                                    use_container_width: bool = True) -> None:
    """One-click favorite-then-open-in-Resume-Clinic (ADR-090 favorite->clinic bridge).

    Favorites the job (if not already) so it joins the clinic's focus list, then
    navigates to the Resume Clinic with a one-shot `clinic_focus_job_id` hint so the
    session opens already focused on this job. Shared by Run report, Opportunity, and
    Matches so the bridge behaves identically everywhere."""
    from app.ui.nav import _navigate  # local import: keep nav a one-way dep of components

    user_id = st.session_state.current_user_id
    if not st.button(label, key=key, use_container_width=use_container_width,
                     help="Favorite this job and open a Resume Clinic session focused on it."):
        return
    if job_id not in favorited_ids(user_id):
        try:
            api.add_favorite(user_id, workflow_id, job_id)
            _cached_favorites.clear()
        except api.FavoritesCapError:
            st.warning(f"You already have {_CAP} favorite jobs (the limit). "
                       "Un-favorite one to add another.")
            return
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not flag this job: {exc}")
            return
    _navigate("Resume Clinic", clinic_focus_job_id=job_id)


def render_favorite_toggle(*, job_id: str, workflow_id: str, key: str,
                           use_container_width: bool = True) -> None:
    """Render the star toggle for one job. Adds to / removes from My favorite jobs
    for the active profile; shows a clear message at the cap."""
    user_id = st.session_state.current_user_id
    is_fav = job_id in favorited_ids(user_id)

    if is_fav:
        if st.button("★ Un-favorite", key=key, use_container_width=use_container_width,
                     help="Remove from My favorite jobs."):
            try:
                api.remove_favorite(user_id, job_id)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not un-favorite: {exc}")
                return
            _cached_favorites.clear()
            st.toast("Removed from My favorite jobs.")
            st.rerun()
    else:
        if st.button("⭐ Favorite", key=key, use_container_width=use_container_width,
                     help="Flag this job to tailor toward in the Resume Clinic."):
            try:
                api.add_favorite(user_id, workflow_id, job_id)
            except api.FavoritesCapError:
                st.warning(
                    f"You already have {_CAP} favorite jobs (the limit). "
                    "Un-favorite one to add another."
                )
                return
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not favorite: {exc}")
                return
            _cached_favorites.clear()
            st.toast("Added to My favorite jobs.")
            st.rerun()
