"""ObservabilityService — typed API over all observability repositories.

All methods swallow repository exceptions and log them. Observability failures
must never crash workflows — a broken audit trail is better than a broken run.
"""
import logging
import uuid
from pathlib import Path

from app.repositories.database import DEFAULT_DB_PATH, utcnow_iso
from app.repositories.decision_repository import DecisionRepository
from app.repositories.observability_repository import ObservabilityRepository
from app.repositories.security_repository import SecurityRepository
from app.repositories.step_repository import StepRepository
from app.state.workflow_state import WorkflowStep

logger = logging.getLogger(__name__)


def emit_security_event_safe(
    workflow_id: str,
    event_type: str,
    severity: str,
    description: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Append one security event from a call site that has no ObservabilityService.

    For the cost-cap emit sites (ADR-073): config-edit validation and kickoff
    override validation run in module-level helpers, the latter before the run
    UUID even exists, so they cannot reach the injected ObservabilityService.
    This constructs a SecurityRepository on the default DB and appends the row,
    swallowing any error — mirroring ObservabilityService.log_security_event's
    never-crash contract (a missing audit row must never break a user action).
    Pair with the SYSTEM_RUN_ID sentinel when there is no real run context.
    """
    try:
        SecurityRepository(db_path).create(
            event_id=str(uuid.uuid4()),
            workflow_run_id=workflow_id,
            event_type=event_type,
            severity=severity,
            description=description,
        )
    except Exception:
        logger.exception("emit_security_event_safe failed")


def log_artifact_decision(
    observability: "ObservabilityService",
    *,
    workflow_id: str | None,
    decision_type: str,
    decision_value: str,
    presented_at: str | None,
    payload: dict,
) -> None:
    """Mirror a human approve/revise/reject/edit decision into the
    `human_decisions` audit trail (ADR-074 Gap 1), alongside the domain-table
    write that already persists it.

    The decision endpoints record the decision in the artifact's own table
    (`tailored_resumes.decision` / `resume_clinic_reviews.decision`); this adds
    the cross-cutting observability/accountability row so "who decided what, when,
    on which artifact" is answerable in one place (ADR-059 / Article 8).

    `payload` must be PII-safe — ids, flags, and counts only, never resume content
    or identifiers. `decided_at` is stamped now; `presented_at` is the artifact's
    creation time. Routes through `log_human_decision`, which swallows errors, so a
    missing audit row never breaks the user's decision.
    """
    observability.log_human_decision(
        workflow_id=workflow_id or "",
        decision_type=decision_type,
        decision_value=str(decision_value),
        payload=payload,
        presented_at=presented_at or utcnow_iso(),
        decided_at=utcnow_iso(),
    )


def fidelity_review_security_description(fidelity: dict | None) -> str | None:
    """Return a PII-safe `unsupported_claim` description if a Fidelity review
    tripped the fabrication guardrail, else None (ADR-073).

    The guardrail trips when the reviewer recommends `reject` OR reports any
    unsupported_claims / fabricated_metrics. The description is counts + status
    ONLY — never the claim text, which can echo resume content. Shared by the
    tailoring router and the resume_clinic_runner so the wording + the
    trip condition are defined and tested in one place.
    """
    if not fidelity:
        return None
    unsupported = len(fidelity.get("unsupported_claims") or [])
    fabricated = len(fidelity.get("fabricated_metrics") or [])
    recommendation = fidelity.get("approval_recommendation")
    if recommendation != "reject" and unsupported == 0 and fabricated == 0:
        return None
    return (
        f"Fidelity flagged {unsupported} unsupported claim(s), "
        f"{fabricated} fabricated metric(s); recommendation={recommendation}"
    )


class ObservabilityService:
    """Single entry point for all observability writes."""

    def __init__(
        self,
        observability_repo: ObservabilityRepository,
        step_repo: StepRepository,
        decision_repo: DecisionRepository,
        security_repo: SecurityRepository,
    ) -> None:
        self._obs = observability_repo
        self._steps = step_repo
        self._decisions = decision_repo
        self._security = security_repo

    # ── Agent events ─────────────────────────────────────────────────────────

    def log_agent_started(
        self, workflow_id: str, agent_name: str, input_summary: str
    ) -> str:
        """Log agent start. Returns event_id for correlation with the completion call."""
        event_id = str(uuid.uuid4())
        try:
            self._obs.create_agent_event(
                event_id=event_id,
                workflow_run_id=workflow_id,
                agent_name=agent_name,
                event_type="started",
                status="running",
                input_summary=input_summary[:500],
            )
        except Exception:
            logger.exception("ObservabilityService: log_agent_started failed")
        return event_id

    def log_agent_completed(
        self,
        workflow_id: str,
        agent_name: str,
        event_id: str,
        output_summary: str,
        duration_ms: int,
    ) -> None:
        try:
            self._obs.create_agent_event(
                event_id=str(uuid.uuid4()),
                workflow_run_id=workflow_id,
                agent_name=agent_name,
                event_type="completed",
                status="completed",
                duration_ms=duration_ms,
                output_summary=output_summary[:500],
            )
        except Exception:
            logger.exception("ObservabilityService: log_agent_completed failed")

    def log_agent_failed(
        self,
        workflow_id: str,
        agent_name: str,
        event_id: str,
        error_message: str,
        duration_ms: int,
    ) -> None:
        try:
            self._obs.create_agent_event(
                event_id=str(uuid.uuid4()),
                workflow_run_id=workflow_id,
                agent_name=agent_name,
                event_type="failed",
                status="failed",
                duration_ms=duration_ms,
                output_summary=error_message[:500],
            )
        except Exception:
            logger.exception("ObservabilityService: log_agent_failed failed")

    # ── LLM calls ────────────────────────────────────────────────────────────

    def log_llm_call(
        self,
        workflow_id: str,
        agent_name: str,
        provider: str,
        model: str,
        tokens_input: int,
        tokens_output: int,
        cost_usd: float,
        latency_ms: int,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> None:
        try:
            self._obs.create_llm_call(
                call_id=str(uuid.uuid4()),
                workflow_run_id=workflow_id,
                agent_name=agent_name,
                provider=provider,
                model=model,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                estimated_cost=cost_usd,
                latency_ms=latency_ms,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
            )
        except Exception:
            logger.exception("ObservabilityService: log_llm_call failed")

    # ── Step transitions ──────────────────────────────────────────────────────

    def log_step_started(self, workflow_id: str, step: WorkflowStep) -> str:
        """Log step start. Returns step_execution_id for the completion call."""
        step_execution_id = str(uuid.uuid4())
        try:
            self._steps.create(step_execution_id, workflow_id, step.value)
        except Exception:
            logger.exception("ObservabilityService: log_step_started failed")
        return step_execution_id

    def log_step_completed(
        self,
        workflow_id: str,
        step_execution_id: str,
        duration_ms: int,
        notes: str | None = None,
    ) -> None:
        try:
            self._steps.complete(step_execution_id, notes=notes)
        except Exception:
            logger.exception("ObservabilityService: log_step_completed failed")

    def log_step_failed(
        self,
        workflow_id: str,
        step_execution_id: str,
        notes: str | None = None,
    ) -> None:
        try:
            self._steps.fail(step_execution_id, notes=notes)
        except Exception:
            logger.exception("ObservabilityService: log_step_failed failed")

    # ── HITL decisions ────────────────────────────────────────────────────────

    def log_human_decision(
        self,
        workflow_id: str,
        decision_type: str,
        decision_value: str,
        payload: dict,
        presented_at: str,
        decided_at: str,
    ) -> None:
        try:
            self._decisions.create(
                decision_id=str(uuid.uuid4()),
                workflow_run_id=workflow_id,
                decision_type=decision_type,
                decision_value=decision_value,
                payload=payload,
                presented_at=presented_at,
                decided_at=decided_at,
            )
        except Exception:
            logger.exception("ObservabilityService: log_human_decision failed")

    # ── Security events ───────────────────────────────────────────────────────

    def log_security_event(
        self,
        workflow_id: str,
        event_type: str,
        severity: str,
        description: str,
    ) -> None:
        try:
            self._security.create(
                event_id=str(uuid.uuid4()),
                workflow_run_id=workflow_id,
                event_type=event_type,
                severity=severity,
                description=description,
            )
        except Exception:
            logger.exception("ObservabilityService: log_security_event failed")

    # ── Run metrics ───────────────────────────────────────────────────────────

    def init_run_metrics(self, workflow_id: str, started_at: str) -> None:
        """Initialise the run_metrics row at workflow start. Wraps the repo
        method so failures are swallowed (observability must never crash runs)."""
        try:
            self._obs.create_run_metrics(
                metrics_id=str(uuid.uuid4()),
                workflow_run_id=workflow_id,
                started_at=started_at,
            )
        except Exception:
            logger.exception("ObservabilityService: init_run_metrics failed")

    def get_llm_calls_by_run(self, workflow_id: str) -> list[dict]:
        """Public pass-through to the llm_calls audit rows for one workflow.

        Used by callers that need per-row filtering the aggregate methods
        below don't provide - e.g. the Resume Clinic chat router needs to
        count `agent_name == "resume_chat"` rows to enforce the per-clinic
        turn cap. Returns [] on any persistence error rather than raising;
        the audit trail is non-load-bearing for runtime correctness.
        """
        try:
            return self._obs.get_llm_calls_by_run(workflow_id) or []
        except Exception:
            logger.exception("ObservabilityService: get_llm_calls_by_run failed")
            return []

    def compute_run_totals_from_llm_calls(self, workflow_id: str) -> dict:
        """Derive canonical totals (calls / tokens / cost) from llm_calls rows.

        This is the truth source — state_json.run_metrics only counts what the
        in-memory aggregator captured and misses retries / interpreter shutdowns.
        Returns {"calls": int, "tokens_input": int, "tokens_output": int, "cost_usd": float}.
        """
        try:
            rows = self._obs.get_llm_calls_by_run(workflow_id)
        except Exception:
            logger.exception("ObservabilityService: get_llm_calls_by_run failed")
            return {"calls": 0, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0}
        return {
            "calls":         len(rows),
            "tokens_input":  sum(int(r.get("tokens_input")  or 0) for r in rows),
            "tokens_output": sum(int(r.get("tokens_output") or 0) for r in rows),
            "cost_usd":      sum(float(r.get("estimated_cost") or 0.0) for r in rows),
        }

    def finalize_run_metrics(
        self,
        workflow_id: str,
        total_llm_calls: int,
        total_tokens_input: int,
        total_tokens_output: int,
        total_cost_usd: float,
        total_duration_ms: int,
        completed_at: str,
    ) -> None:
        try:
            self._obs.update_run_metrics(
                workflow_run_id=workflow_id,
                total_llm_calls=total_llm_calls,
                total_tokens_input=total_tokens_input,
                total_tokens_output=total_tokens_output,
                total_cost=total_cost_usd,
                total_duration_ms=total_duration_ms,
                completed_at=completed_at,
            )
        except Exception:
            logger.exception("ObservabilityService: finalize_run_metrics failed")
