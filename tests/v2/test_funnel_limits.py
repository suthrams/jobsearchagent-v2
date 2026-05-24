"""ADR-061: configurable funnel-width cap helpers in app/workflows/limits.py."""
from __future__ import annotations

from app.workflows.limits import (
    MAX_DISCOVERED_JOBS,
    MAX_JOBS_PER_RUN,
    MAX_SCORED_CEILING,
    get_max_discovered_jobs,
    get_max_scored,
)


def _state(scoring=None, search=None):
    cfg = {}
    if scoring is not None:
        cfg["scoring"] = scoring
    if search is not None:
        cfg["search"] = search
    return {"effective_config": cfg}


def test_get_max_scored_defaults_when_unset():
    assert get_max_scored({}) == MAX_JOBS_PER_RUN
    assert get_max_scored(_state()) == MAX_JOBS_PER_RUN


def test_get_max_scored_honors_config():
    assert get_max_scored(_state(scoring={"max_scored": 18})) == 18


def test_get_max_scored_clamped_to_ceiling():
    assert get_max_scored(_state(scoring={"max_scored": 999})) == MAX_SCORED_CEILING


def test_get_max_scored_floor_is_one():
    assert get_max_scored(_state(scoring={"max_scored": 0})) == 1


def test_get_max_scored_handles_bad_value():
    assert get_max_scored(_state(scoring={"max_scored": "lots"})) == MAX_JOBS_PER_RUN


def test_discovery_equals_scored_in_auto_mode():
    # No manual_selection -> discovery cap follows the scored cap.
    assert get_max_discovered_jobs(_state(scoring={"max_scored": 15})) == 15


def test_discovery_wide_net_in_manual_mode():
    state = _state(
        scoring={"manual_selection": True},
        search={"max_discovered": 40},
    )
    assert get_max_discovered_jobs(state) == 40


def test_discovery_manual_default_is_ceiling():
    state = _state(scoring={"manual_selection": True})
    assert get_max_discovered_jobs(state) == MAX_DISCOVERED_JOBS


def test_discovery_manual_clamped_to_ceiling():
    state = _state(
        scoring={"manual_selection": True},
        search={"max_discovered": 999},
    )
    assert get_max_discovered_jobs(state) == MAX_DISCOVERED_JOBS
