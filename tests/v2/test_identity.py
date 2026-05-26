"""Tests for the identity seam (ADR-062, app/api/identity.py).

The seam is the one place the system decides "who is the current user?". These
tests pin its contract so the future auth swap has a clear spec to preserve.
"""
import pytest
from fastapi import HTTPException

from app.api.identity import get_current_user_id, set_user_validator


@pytest.fixture(autouse=True)
def _reset_validator():
    """Each test starts with no validator (accept-any), and restores that after."""
    set_user_validator(None)
    yield
    set_user_validator(None)


def test_absent_resolves_to_default_profile():
    assert get_current_user_id(None) == "0"


def test_blank_resolves_to_default_profile():
    assert get_current_user_id("   ") == "0"


def test_value_is_trimmed_and_passed_through():
    assert get_current_user_id("  7 ") == "7"


def test_unknown_id_rejected_when_validator_installed():
    set_user_validator(lambda uid: uid == "0")
    # known id passes
    assert get_current_user_id("0") == "0"
    # unknown id is rejected fast
    with pytest.raises(HTTPException) as exc:
        get_current_user_id("999")
    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "unknown_user"


def test_validator_not_consulted_for_default_fallback():
    """A missing param resolves to the default profile without hitting the
    validator — the default user always exists by construction."""
    calls = []
    set_user_validator(lambda uid: calls.append(uid) or True)
    assert get_current_user_id(None) == "0"
    assert calls == []
