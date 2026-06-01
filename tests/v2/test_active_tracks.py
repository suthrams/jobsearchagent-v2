"""ADR-071: per-profile active scoring tracks.

Covers the limits.py helpers, the qualification correctness invariant (a job that
clears the threshold ONLY on an inactive track must NOT qualify), the JobScore
optional-track schema, ConfigService validation, and that score_jobs threads the
active set into the scoring context.
"""
from __future__ import annotations

from app.schemas.job_score import JobScore
from app.workflows.limits import (
    TRACK_TO_SCORE_KEY,
    VALID_TRACKS,
    active_track_keys,
    best_track_score,
    get_active_tracks,
    qualifies_for_deep_review,
)


def _state(tracks=None):
    scoring = {} if tracks is None else {"tracks": tracks}
    return {"effective_config": {"scoring": scoring}}


# ── get_active_tracks ─────────────────────────────────────────────────────────

def test_active_tracks_default_is_all_three():
    assert get_active_tracks({}) == list(VALID_TRACKS)
    assert get_active_tracks(_state()) == list(VALID_TRACKS)
    assert get_active_tracks(_state(tracks=None)) == list(VALID_TRACKS)


def test_active_tracks_honors_subset():
    assert get_active_tracks(_state(tracks=["ic"])) == ["ic"]
    assert get_active_tracks(_state(tracks=["ic", "architect"])) == ["ic", "architect"]


def test_active_tracks_canonical_order_regardless_of_input_order():
    # Input order is normalised to VALID_TRACKS order so the set is deterministic.
    assert get_active_tracks(_state(tracks=["management", "ic"])) == ["ic", "management"]


def test_active_tracks_drops_unknown_names():
    assert get_active_tracks(_state(tracks=["ic", "bogus", "manager"])) == ["ic"]


def test_active_tracks_empty_or_all_invalid_falls_back_to_all():
    assert get_active_tracks(_state(tracks=[])) == list(VALID_TRACKS)
    assert get_active_tracks(_state(tracks=["nope"])) == list(VALID_TRACKS)


def test_active_tracks_non_list_falls_back_to_all():
    assert get_active_tracks(_state(tracks="ic")) == list(VALID_TRACKS)


def test_active_track_keys_maps_to_score_fields():
    assert active_track_keys(_state(tracks=["ic"])) == ("technical_score",)
    assert active_track_keys(_state(tracks=["architect", "management"])) == (
        "architecture_score",
        "leadership_score",
    )
    # default = all three
    assert active_track_keys({}) == (
        "technical_score",
        "architecture_score",
        "leadership_score",
    )


def test_track_to_score_key_map_is_complete():
    assert set(TRACK_TO_SCORE_KEY) == set(VALID_TRACKS)


# ── best_track_score / qualifies_for_deep_review ──────────────────────────────

def test_best_track_score_default_considers_all_three():
    job = {"technical_score": 10, "architecture_score": 20, "leadership_score": 90}
    assert best_track_score(job) == 90


def test_best_track_score_restricted_to_active_keys():
    job = {"technical_score": 10, "architecture_score": 20, "leadership_score": 90}
    assert best_track_score(job, active_track_keys(_state(tracks=["ic"]))) == 10


def test_best_track_score_treats_none_as_zero():
    job = {"technical_score": None, "architecture_score": None, "leadership_score": 80}
    keys = active_track_keys(_state(tracks=["ic", "architect"]))
    assert best_track_score(job, keys) == 0


def test_inactive_track_does_not_qualify_job():
    """Core ADR-071 correctness: a job clearing the threshold ONLY on an inactive
    track must not be pulled into deep review for a single-track profile."""
    job = {"technical_score": 10, "architecture_score": 20, "leadership_score": 90}
    ic_keys = active_track_keys(_state(tracks=["ic"]))
    # all-tracks default: qualifies (leadership clears)
    assert qualifies_for_deep_review(job, 75) is True
    # ic-only: leadership is irrelevant -> does NOT qualify
    assert qualifies_for_deep_review(job, 75, ic_keys) is False


def test_active_track_clearing_threshold_qualifies():
    job = {"technical_score": 88, "architecture_score": None, "leadership_score": None}
    ic_keys = active_track_keys(_state(tracks=["ic"]))
    assert qualifies_for_deep_review(job, 75, ic_keys) is True


# ── JobScore schema ───────────────────────────────────────────────────────────

def test_job_score_accepts_null_track_scores():
    score = JobScore(
        job_id="j1",
        resume_id="r1",
        overall_score=80,
        technical_score=80,
        architecture_score=None,
        leadership_score=None,
        domain_score=70,
        match_summary="ok",
        strengths=["x"],
        gaps=["y"],
        recommended_next_action="apply",
        confidence=70,
    )
    assert score.architecture_score is None
    assert score.leadership_score is None
    assert score.technical_score == 80


def test_job_score_track_fields_default_to_none():
    score = JobScore(
        job_id="j1",
        resume_id="r1",
        overall_score=80,
        domain_score=70,
        match_summary="ok",
        strengths=[],
        gaps=[],
        recommended_next_action="apply",
        confidence=70,
    )
    assert score.technical_score is None
    assert score.architecture_score is None
    assert score.leadership_score is None


def test_job_score_required_fields_still_required():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobScore(
            job_id="j1",
            resume_id="r1",
            # overall_score missing
            domain_score=70,
            match_summary="ok",
            strengths=[],
            gaps=[],
            recommended_next_action="apply",
            confidence=70,
        )


# ── ConfigService validation ──────────────────────────────────────────────────

def test_config_service_validates_tracks():
    from app.services.config_service import ConfigService

    svc = ConfigService.__new__(ConfigService)
    assert svc._enforce_limits({"scoring": {"tracks": ["architect", "ic"]}})["scoring"][
        "tracks"
    ] == ["ic", "architect"]
    # unknown names dropped
    assert svc._enforce_limits({"scoring": {"tracks": ["ic", "x"]}})["scoring"][
        "tracks"
    ] == ["ic"]
    # all-invalid -> key removed (default all-three applies downstream)
    assert "tracks" not in svc._enforce_limits({"scoring": {"tracks": ["x"]}})["scoring"]
    # non-list -> key removed
    assert "tracks" not in svc._enforce_limits({"scoring": {"tracks": "ic"}})["scoring"]


def test_tracks_is_not_a_protected_key():
    from app.services.config_service import _PROTECTED_KEYS

    assert "scoring.tracks" not in _PROTECTED_KEYS


# ── auto_select_jobs node honors active tracks ────────────────────────────────

def _scored_job(job_id, technical, architecture, leadership):
    return {
        "id": job_id,
        "job_id": job_id,
        "status": "scored",
        "technical_score": technical,
        "architecture_score": architecture,
        "leadership_score": leadership,
    }


def test_auto_select_excludes_jobs_qualifying_only_on_inactive_track():
    """A job that clears the threshold solely on leadership must NOT be auto-selected
    for an IC-only profile (ADR-071)."""
    from app.workflows.nodes.await_job_selection import make_await_job_selection_node

    node = make_await_job_selection_node()
    # job A clears on leadership only; job B clears on technical (active for IC).
    state = {
        "scored_jobs": [
            _scored_job("A", technical=10, architecture=20, leadership=90),
            _scored_job("B", technical=88, architecture=None, leadership=None),
        ],
        "effective_config": {"scoring": {"min_match_score": 75, "tracks": ["ic"]}},
    }
    result = node(state)
    selected_ids = {j["job_id"] for j in result["selected_jobs"]}
    assert selected_ids == {"B"}


def test_auto_select_default_all_tracks_selects_leadership_job():
    """Same job set, but the default (all tracks) profile selects both."""
    from app.workflows.nodes.await_job_selection import make_await_job_selection_node

    node = make_await_job_selection_node()
    state = {
        "scored_jobs": [
            _scored_job("A", technical=10, architecture=20, leadership=90),
            _scored_job("B", technical=88, architecture=70, leadership=60),
        ],
        "effective_config": {"scoring": {"min_match_score": 75}},  # no tracks -> all
    }
    result = node(state)
    selected_ids = {j["job_id"] for j in result["selected_jobs"]}
    assert selected_ids == {"A", "B"}
