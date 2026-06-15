"""Fail-loud deployment-safety guard tests (ADR-106).

Forcing-function coverage for the startup tripwire that refuses to boot under an unsafe
deployment topology (multi-worker / non-loopback bind). The detector is pure (argv + env
injected), so these assert the exact signal->violation mapping without a live server,
plus the enforce() raise/override behavior.
"""
from __future__ import annotations

import pytest

from app.api.deployment_guard import (
    OVERRIDE_ENV_VAR,
    UnsafeDeploymentError,
    detect_unsafe_deployment,
    enforce_deployment_safety,
)

_SAFE_ARGV = ["uvicorn", "app.api.main:app"]


# --- safe baselines -----------------------------------------------------------------

def test_clean_argv_and_env_is_safe():
    assert detect_unsafe_deployment(_SAFE_ARGV, {}) == []


def test_reload_dev_launch_is_safe():
    # The documented dev command must never trip the guard.
    assert detect_unsafe_deployment(["uvicorn", "app.api.main:app", "--reload"], {}) == []


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST"])
def test_loopback_hosts_are_safe(host):
    assert detect_unsafe_deployment(["uvicorn", "app", "--host", host], {}) == []


def test_single_worker_is_safe():
    assert detect_unsafe_deployment(["uvicorn", "app", "--workers", "1"], {}) == []
    assert detect_unsafe_deployment(_SAFE_ARGV, {"WEB_CONCURRENCY": "1"}) == []


# --- multi-worker violations --------------------------------------------------------

def test_web_concurrency_gt_1_is_unsafe():
    violations = detect_unsafe_deployment(_SAFE_ARGV, {"WEB_CONCURRENCY": "4"})
    assert len(violations) == 1
    assert "WEB_CONCURRENCY" in violations[0]


@pytest.mark.parametrize("argv", [
    ["uvicorn", "app", "--workers", "2"],
    ["uvicorn", "app", "--workers=3"],
])
def test_multiple_workers_flag_is_unsafe(argv):
    violations = detect_unsafe_deployment(argv, {})
    assert any("worker" in v.lower() for v in violations)


def test_garbage_web_concurrency_is_ignored():
    # A non-integer value must not crash and must not be treated as > 1.
    assert detect_unsafe_deployment(_SAFE_ARGV, {"WEB_CONCURRENCY": "abc"}) == []


# --- non-loopback bind violations ---------------------------------------------------

@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
def test_non_loopback_host_is_unsafe(host):
    violations = detect_unsafe_deployment(["uvicorn", "app", "--host", host], {})
    assert any("non-loopback" in v for v in violations)


def test_host_equals_form_is_parsed():
    violations = detect_unsafe_deployment(["uvicorn", "app", "--host=0.0.0.0"], {})
    assert any("non-loopback" in v for v in violations)


def test_last_host_occurrence_wins():
    # A later --host overrides an earlier one (argparse semantics).
    assert detect_unsafe_deployment(
        ["uvicorn", "app", "--host", "0.0.0.0", "--host", "127.0.0.1"], {}) == []


# --- combined signals accumulate ----------------------------------------------------

def test_combined_signals_accumulate():
    violations = detect_unsafe_deployment(
        ["uvicorn", "app", "--host", "0.0.0.0", "--workers", "4"],
        {"WEB_CONCURRENCY": "4"},
    )
    assert len(violations) == 3


# --- enforce(): raise vs override ---------------------------------------------------

def test_enforce_noop_when_safe():
    # Must not raise.
    enforce_deployment_safety(_SAFE_ARGV, {})


def test_enforce_raises_on_violation():
    with pytest.raises(UnsafeDeploymentError) as exc:
        enforce_deployment_safety(["uvicorn", "app", "--host", "0.0.0.0"], {})
    # The error must explain how to override.
    assert OVERRIDE_ENV_VAR in str(exc.value)


@pytest.mark.parametrize("flag", ["1", "true", "YES", "on"])
def test_enforce_override_downgrades_to_warning(flag):
    # With the escape hatch set, an unsafe topology starts (no raise).
    enforce_deployment_safety(
        ["uvicorn", "app", "--host", "0.0.0.0"],
        {OVERRIDE_ENV_VAR: flag},
    )


def test_enforce_override_falsey_still_raises():
    with pytest.raises(UnsafeDeploymentError):
        enforce_deployment_safety(
            ["uvicorn", "app", "--host", "0.0.0.0"],
            {OVERRIDE_ENV_VAR: "0"},
        )
