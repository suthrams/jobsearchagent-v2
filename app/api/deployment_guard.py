"""Fail-loud startup guard for unsafe deployment topologies (ADR-106).

The whole runtime is single-process and cooperative-trust: the workflow executor,
the idempotency / run-control registries, and run recovery are all in-memory, and
identity is an unauthenticated ``?user_id=`` query param with no ownership checks.
Those are correct scope cuts for the intended single-user, loopback deployment, but
nothing trips when a deployment violates them (the "silent deployment cliff" from the
2026-06-13 architecture review, roadmap item 4):

- ``--workers 2`` / ``WEB_CONCURRENCY>1`` -> per-worker registries -> double-runs and
  double-spend.
- a non-loopback bind (``--host 0.0.0.0``) with no auth -> cross-tenant read/write/delete.

This module detects those two misconfigurations from process-visible state and, by
default, refuses to start. It is a best-effort tripwire for the common cases, NOT a
sandbox: heuristic parsing of ``sys.argv`` + env does not catch every launch method
(e.g. programmatic ``uvicorn.run(host=...)`` or a gunicorn config-file ``workers``).
Set ``ALLOW_UNSAFE_DEPLOYMENT=1`` to downgrade the hard failure to a prominent warning.
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

# A bind host that keeps the server reachable only from the local machine. uvicorn's
# default (no --host) is 127.0.0.1, which is safe and produces no violation.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Env var that opts out of the hard failure (downgrades to a logged warning).
OVERRIDE_ENV_VAR = "ALLOW_UNSAFE_DEPLOYMENT"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class UnsafeDeploymentError(RuntimeError):
    """Raised at startup when an unsafe deployment topology is detected and the
    operator has not explicitly opted in via ALLOW_UNSAFE_DEPLOYMENT."""


def _arg_value(argv: Sequence[str], flag: str) -> str | None:
    """Return the value of ``--flag value`` or ``--flag=value`` in argv, else None.

    Returns the LAST occurrence so a later override on the command line wins, matching
    how argument parsers resolve repeated flags.
    """
    value: str | None = None
    prefix = f"{flag}="
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            value = argv[i + 1]
        elif tok.startswith(prefix):
            value = tok[len(prefix):]
    return value


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def detect_unsafe_deployment(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Detect unsafe deployment signals from process-visible state.

    Pure (inputs injected) so it is unit-testable without a live server. Returns a list
    of human-readable violation strings; an empty list means "safe as far as we can
    tell". Detects:

    - multi-worker intent: ``WEB_CONCURRENCY`` > 1, or ``--workers N`` (N > 1) in argv.
    - non-loopback bind: ``--host H`` in argv where H is not a loopback host.
    """
    argv = list(sys.argv if argv is None else argv)
    environ = os.environ if environ is None else environ

    violations: list[str] = []

    # --- multi-worker -------------------------------------------------------------
    web_concurrency = _as_int(environ.get("WEB_CONCURRENCY"))
    if web_concurrency is not None and web_concurrency > 1:
        violations.append(
            f"WEB_CONCURRENCY={web_concurrency} requests multiple workers; this app's "
            "idempotency, single-flight, cancellation, and run-recovery registries are "
            "in-process and are NOT shared across workers (double-runs / double-spend)."
        )

    workers = _as_int(_arg_value(argv, "--workers"))
    if workers is not None and workers > 1:
        violations.append(
            f"--workers {workers} runs multiple worker processes; the in-process "
            "run-control / idempotency / recovery registries are not shared across them "
            "(double-runs / double-spend)."
        )

    # --- non-loopback bind --------------------------------------------------------
    host = _arg_value(argv, "--host")
    if host is not None and host.strip().lower() not in LOOPBACK_HOSTS:
        violations.append(
            f"--host {host} binds a non-loopback address; identity is an "
            "unauthenticated ?user_id= param with no ownership checks (ADR-062), so "
            "exposing the port allows cross-tenant read/write/delete."
        )

    return violations


def enforce_deployment_safety(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Fail loud (or warn, if opted out) when an unsafe deployment is detected.

    Called once at startup BEFORE expensive wiring. Raises ``UnsafeDeploymentError``
    when violations are found, unless ``ALLOW_UNSAFE_DEPLOYMENT`` is truthy, in which
    case it logs a prominent warning and returns so the operator can proceed knowingly.
    """
    environ = os.environ if environ is None else environ
    violations = detect_unsafe_deployment(argv, environ)
    if not violations:
        return

    bullet_list = "\n".join(f"  - {v}" for v in violations)
    override_on = environ.get(OVERRIDE_ENV_VAR, "").strip().lower() in _TRUTHY

    if override_on:
        logger.warning(
            "UNSAFE DEPLOYMENT detected but %s is set - starting anyway:\n%s",
            OVERRIDE_ENV_VAR, bullet_list,
        )
        return

    raise UnsafeDeploymentError(
        "Refusing to start: this application is single-process and "
        "cooperative-trust by design (ADR-062/082/083/096/106) and is only safe run "
        "as a single worker bound to loopback.\n" + bullet_list +
        f"\nFix the launch command, or set {OVERRIDE_ENV_VAR}=1 to override (you accept "
        "the double-spend / cross-tenant risk)."
    )
