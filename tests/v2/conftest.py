"""Shared pytest fixtures for the v2 test suite."""
import pytest


@pytest.fixture(autouse=True)
def _no_observability_writes_to_real_db(monkeypatch):
    """Stop the app-global "safe" observability helpers from writing to the REAL
    data/v2.db during tests.

    `record_api_request_safe` (the ADR-074 HTTP middleware) and
    `emit_security_event_safe` (the ADR-073 run-less cost-cap helper) both default
    to `db_path=DEFAULT_DB_PATH`, and they are invoked from app-global call sites
    (the middleware; the config/workflows cost-cap validators) that bypass the
    per-test dependency/db injection. Without this fixture every TestClient request
    - including all the deliberate negative-path assertions - and every cost-cap
    emit lands in the production observability tables, polluting the System
    Dashboard's API error rate and security counts.

    No-op the helpers at the bindings the call sites actually resolve (each module
    did `from ... import <helper>`, so patching the source module is not enough).
    Tests that intentionally exercise these helpers install their own override
    (test_api_requests monkeypatches main.record_api_request_safe to capture;
    test_security_events calls emit_security_event_safe via its own import with a
    temp db_path), which wins inside the test body.
    """
    import app.api.main as _main
    import app.api.routers.config as _config
    import app.api.routers.workflows as _workflows

    monkeypatch.setattr(_main, "record_api_request_safe",
                        lambda **kw: None, raising=False)
    monkeypatch.setattr(_config, "emit_security_event_safe",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(_workflows, "emit_security_event_safe",
                        lambda *a, **k: None, raising=False)
    yield


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
