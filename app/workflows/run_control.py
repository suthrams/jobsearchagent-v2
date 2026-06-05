"""Process-local run-lifecycle control: single-flight guard + cooperative cancel.

Shared by ADR-082 (in-flight execution guard) and ADR-083 (run cancellation).

Both registries are in-memory and lock-protected. They are authoritative for the
current single-process deployment: the FastAPI app and the workflow thread pool
live in the same process, so a running graph thread and the request handlers see
the same registry. A multi-worker deployment would need a shared store (a Redis or
DB advisory lock); that is out of scope and flagged in both ADRs.
"""
from __future__ import annotations

import threading


class WorkflowCancelled(Exception):
    """Raised inside a running graph (at a node boundary) when a cancel was
    requested for the run. Caught by the run wrappers, which finalize the run to
    the terminal ``cancelled`` status (ADR-083)."""


# ── In-flight execution guard (ADR-082) ─────────────────────────────────────────
# Single-flight set of workflow_ids that currently have a graph executing in the
# thread pool. Closes the read-then-act double-submit races on the re-entry
# endpoints (retry / scoring), where the natural dedup key is the workflow_id.

_running_lock = threading.Lock()
_running: set[str] = set()


def try_acquire_running(workflow_id: str) -> bool:
    """Atomically claim a workflow_id as executing.

    Returns True if the caller acquired the slot (it may submit the run), or
    False if the id is already executing (the caller must reject with 409).
    """
    with _running_lock:
        if workflow_id in _running:
            return False
        _running.add(workflow_id)
        return True


def release_running(workflow_id: str) -> None:
    """Release a workflow_id's execution slot. Safe to call when not held."""
    with _running_lock:
        _running.discard(workflow_id)


def is_running(workflow_id: str) -> bool:
    with _running_lock:
        return workflow_id in _running


# ── Cooperative cancellation (ADR-083) ──────────────────────────────────────────
# Set of workflow_ids for which a cancel has been requested. _instrument_step
# checks this at each node boundary and raises WorkflowCancelled before the next
# node runs. The flag is cleared by the run wrappers once the run unwinds.

_cancel_lock = threading.Lock()
_cancel_requested: set[str] = set()


def request_cancel(workflow_id: str) -> None:
    """Mark a run for cancellation. Idempotent."""
    with _cancel_lock:
        _cancel_requested.add(workflow_id)


def is_cancel_requested(workflow_id: str) -> bool:
    with _cancel_lock:
        return workflow_id in _cancel_requested


def clear_cancel(workflow_id: str) -> None:
    """Drop a run's cancel flag. Safe to call when not set."""
    with _cancel_lock:
        _cancel_requested.discard(workflow_id)
