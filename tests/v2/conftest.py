"""Shared pytest fixtures for the v2 test suite."""
import pytest


@pytest.fixture(autouse=True)
def _reset_identity_validator():
    """Reset the process-global identity validator (ADR-062) before and after
    every test.

    The validator is wired once at app startup (lifespan -> build_and_cache_graph
    in real-deps mode) to the real data/v2.db. Without this reset, that global
    state leaks across tests: a test that triggers startup pins the validator to
    the real DB, and a later test passing a profile id that only exists in its
    own temp DB gets a spurious 404. Identity validation itself is covered
    explicitly in test_identity.py, which installs its own validator per test.
    """
    from app.api.identity import set_user_validator
    set_user_validator(None)
    yield
    set_user_validator(None)
