"""StatusManager — validates all status transitions for workflows and jobs.

Stateless: no DB dependency. Validates a proposed transition and returns the new
status, or raises InvalidTransitionError. The orchestrator writes the result.
"""
from enum import Enum

from app.state.workflow_state import WorkflowStatus


class JobStatus(str, Enum):
    """Lifecycle of a job through the v2 workflow pipeline."""

    DISCOVERED = "discovered"    # found by scraper, not yet scored
    SCORED = "scored"            # Scoring Agent has run
    SHORTLISTED = "shortlisted"  # user selected for deep review
    REVIEWED = "reviewed"        # deep review complete
    APPLIED = "applied"          # user submitted application
    PASSED = "passed"            # user chose not to apply (terminal)
    REJECTED = "rejected"        # company rejection received (terminal)
    OFFER = "offer"              # offer extended (terminal)


_WORKFLOW_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.INITIALIZED: frozenset({WorkflowStatus.RUNNING}),
    WorkflowStatus.RUNNING: frozenset({
        WorkflowStatus.WAITING_FOR_USER,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    }),
    WorkflowStatus.WAITING_FOR_USER: frozenset({
        WorkflowStatus.RUNNING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    }),
    WorkflowStatus.COMPLETED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.CANCELLED: frozenset(),
}

_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.DISCOVERED: frozenset({JobStatus.SCORED}),
    JobStatus.SCORED: frozenset({JobStatus.SHORTLISTED, JobStatus.PASSED}),
    JobStatus.SHORTLISTED: frozenset({JobStatus.REVIEWED}),
    JobStatus.REVIEWED: frozenset({JobStatus.APPLIED, JobStatus.PASSED}),
    JobStatus.APPLIED: frozenset({JobStatus.REJECTED, JobStatus.OFFER}),
    JobStatus.PASSED: frozenset(),
    JobStatus.REJECTED: frozenset(),
    JobStatus.OFFER: frozenset(),
}

_TERMINAL_WORKFLOW: frozenset[WorkflowStatus] = frozenset({
    WorkflowStatus.COMPLETED,
    WorkflowStatus.FAILED,
    WorkflowStatus.CANCELLED,
})

_TERMINAL_JOB: frozenset[JobStatus] = frozenset({
    JobStatus.PASSED,
    JobStatus.REJECTED,
    JobStatus.OFFER,
})


class InvalidTransitionError(Exception):
    """Raised when a requested status transition is not in the allowed set."""


class StatusManager:
    """Validates workflow and job status transitions. Stateless — no internal state."""

    def transition_workflow(
        self,
        workflow_id: str,
        current_status: WorkflowStatus,
        new_status: WorkflowStatus,
    ) -> WorkflowStatus:
        """Return new_status if the transition is allowed; raise InvalidTransitionError otherwise."""
        allowed = _WORKFLOW_TRANSITIONS.get(current_status, frozenset())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"workflow '{workflow_id}': {current_status.value!r} → {new_status.value!r} "
                f"is not allowed. Allowed: {[s.value for s in allowed]}"
            )
        return new_status

    def transition_job(
        self,
        job_id: str,
        current_status: JobStatus,
        new_status: JobStatus,
    ) -> JobStatus:
        """Return new_status if the transition is allowed; raise InvalidTransitionError otherwise."""
        allowed = _JOB_TRANSITIONS.get(current_status, frozenset())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"job '{job_id}': {current_status.value!r} → {new_status.value!r} "
                f"is not allowed. Allowed: {[s.value for s in allowed]}"
            )
        return new_status

    def is_terminal_workflow(self, status: WorkflowStatus) -> bool:
        return status in _TERMINAL_WORKFLOW

    def is_terminal_job(self, status: JobStatus) -> bool:
        return status in _TERMINAL_JOB
