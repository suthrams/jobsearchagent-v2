"""Security-event wiring tests (ADR-073).

Three layers, matching feedback_test_invariants_for_critical_concerns (a dead
subsystem is exactly the failure to guard against here — the table + repo +
ObservabilityService.log_security_event existed for many ADRs with zero emit
sites):

  1. Forcing-function invariant — log_security_event / emit_security_event_safe
     must have call sites in app/ (so the subsystem cannot silently go dead).
  2. PII-safety — emitted descriptions carry counts / field names / reason
     classes only, never resume content or candidate identifiers.
  3. Behavioral — each emit site writes the expected event; the system-level
     read scopes by profile and COALESCEs sentinel/orphan rows to "0".
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.repositories.database import SYSTEM_RUN_ID, init_db
from app.repositories.security_repository import SecurityRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services import system_health as sh
from app.services.custom_url_scraper import CustomUrlScraper
from app.services.observability_service import (
    emit_security_event_safe,
    fidelity_review_security_description,
)
from app.workflows.nodes.load_resume import _emit_pii_redaction

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_v2.db"
    init_db(path)
    return path


# ── Layer 1: forcing-function invariant ───────────────────────────────────────


def test_log_security_event_has_emit_sites():
    """log_security_event must be CALLED somewhere in app/, not only defined.

    Without this guard the whole security_events subsystem can silently revert to
    dead infrastructure (its state for every ADR before 073). Mirrors the
    cost-observability and test_ui_undefined_names forcing functions.
    """
    hits = 0
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # Count call sites, excluding the method DEFINITION in the service.
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("def "):
                continue
            if "log_security_event(" in line or "emit_security_event_safe(" in line:
                hits += 1
    assert hits >= 4, (
        f"expected security-event emit sites across the codebase, found {hits}. "
        "The security_events subsystem has gone dead (ADR-073)."
    )


# ── Layer 2: PII-safety of descriptions ───────────────────────────────────────


def test_fidelity_description_never_leaks_claim_text():
    """The fabrication-guardrail description is counts + status only — never the
    claim text, which can echo resume content."""
    fidelity = {
        "approval_recommendation": "reject",
        "unsupported_claims": ["Led a team of 40 at SecretCorp", "PhD from MIT"],
        "fabricated_metrics": ["Increased revenue 300%"],
    }
    desc = fidelity_review_security_description(fidelity)
    assert desc is not None
    assert "2 unsupported" in desc and "1 fabricated" in desc
    assert "recommendation=reject" in desc
    for leak in ("SecretCorp", "MIT", "revenue", "40"):
        assert leak not in desc


def test_fidelity_description_none_when_clean():
    assert fidelity_review_security_description(
        {"approval_recommendation": "approve", "unsupported_claims": [], "fabricated_metrics": []}
    ) is None
    assert fidelity_review_security_description(None) is None


def test_pii_redaction_description_lists_fields_not_values():
    """pii_redacted records WHICH identifier fields were stripped, never their
    values."""
    obs = MagicMock()
    profile = {
        "name": "Jane Q. Candidate",
        "email": "jane@example.com",
        "location": "Atlanta, GA",
        "raw_text": "JANE Q CANDIDATE full resume blob",
        "skills": ["Python"],
    }
    _emit_pii_redaction(obs, "wf-1", profile)
    obs.log_security_event.assert_called_once()
    kwargs = obs.log_security_event.call_args.kwargs
    assert kwargs["event_type"] == "pii_redacted"
    assert kwargs["severity"] == "info"
    desc = kwargs["description"]
    # field NAMES present...
    for field in ("name", "email", "location", "raw_text"):
        assert field in desc
    # ...values absent
    for leak in ("Jane", "jane@example.com", "Atlanta"):
        assert leak not in desc


def test_pii_redaction_no_event_when_nothing_present():
    obs = MagicMock()
    _emit_pii_redaction(obs, "wf-1", {"skills": ["Python"], "summary": "x"})
    obs.log_security_event.assert_not_called()


# ── Layer 3: behavioral per emit site ─────────────────────────────────────────


def test_ssrf_block_emits_blocked_url_fetch():
    """A user-supplied loopback URL is rejected by the SSRF guard and recorded as
    a high-severity blocked_url_fetch event."""
    obs = MagicMock()
    scraper = CustomUrlScraper(
        ["http://localhost/admin"], llm_client=None,
        observability=obs, workflow_id="wf-ssrf",
    )
    jobs = scraper.scrape()
    assert jobs == []  # blocked, nothing extracted
    obs.log_security_event.assert_called_once()
    kwargs = obs.log_security_event.call_args.kwargs
    assert kwargs["workflow_id"] == "wf-ssrf"
    assert kwargs["event_type"] == "blocked_url_fetch"
    assert kwargs["severity"] == "high"
    assert "host=localhost" in kwargs["description"]


def test_ssrf_block_noop_without_observability():
    """No observability wired -> the scraper still blocks, just no audit row."""
    scraper = CustomUrlScraper(["http://127.0.0.1/"], llm_client=None)
    assert scraper.scrape() == []  # must not raise


def test_cost_cap_emit_writes_sentinel_row(db_path):
    """emit_security_event_safe (the run-less helper) writes a cost_cap_violation
    under the SYSTEM_RUN_ID sentinel."""
    emit_security_event_safe(
        SYSTEM_RUN_ID, "cost_cap_violation", "warning",
        "Rejected cost-cap violation: agent=scoring_agent model=opus",
        db_path=db_path,
    )
    rows = SecurityRepository(db_path).get_by_run(SYSTEM_RUN_ID)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "cost_cap_violation"
    assert rows[0]["severity"] == "warning"


def test_emit_security_event_safe_never_raises(tmp_path):
    """A bad DB path degrades to a no-op, never an exception (never-crash)."""
    emit_security_event_safe(
        "wf", "x", "info", "y", db_path=tmp_path / "nonexistent" / "missing.db"
    )  # must not raise


# ── Layer 3: system-level read scoping (ADR-062 + sentinel) ──────────────────


def _seed(db_path):
    wf_repo = WorkflowRepository(db_path)
    sec = SecurityRepository(db_path)
    # run owned by profile "1"
    wf_repo.create("run-u1", "job_search", {"user_id": "1", "status": "completed"})
    # run owned by profile "0"
    wf_repo.create("run-u0", "job_search", {"user_id": "0", "status": "completed"})
    sec.create("e1", "run-u1", "blocked_url_fetch", "high", "host=x")
    sec.create("e2", "run-u0", "pii_redacted", "info", "name")
    # run-less sentinel event (no workflow_runs row) -> COALESCE to "0"
    sec.create("e3", SYSTEM_RUN_ID, "cost_cap_violation", "warning", "agent=scoring")


def test_list_for_user_scopes_by_profile(db_path):
    _seed(db_path)
    sec = SecurityRepository(db_path)
    u1 = sec.list_for_user("1")
    assert {r["id"] for r in u1} == {"e1"}
    # profile "0" sees its own event AND the sentinel (COALESCE -> "0")
    u0 = sec.list_for_user("0")
    assert {r["id"] for r in u0} == {"e2", "e3"}
    # all profiles
    everyone = sec.list_for_user()
    assert {r["id"] for r in everyone} == {"e1", "e2", "e3"}


def test_security_summary_counts(db_path):
    _seed(db_path)
    summ = sh.security_summary(user_id=None, db_path=db_path)
    assert summ["total"] == 3
    assert summ["by_severity"] == {"high": 1, "warning": 1, "info": 1}
    types = {d["event_type"] for d in summ["by_type"]}
    assert types == {"blocked_url_fetch", "pii_redacted", "cost_cap_violation"}


def test_profiles_overview_groups_and_labels(db_path):
    _seed(db_path)
    rows = sh.profiles_overview(db_path=db_path)
    by_uid = {r["user_id"]: r for r in rows}
    assert by_uid["0"]["name"] == "Primary"      # init_db seeds id 0 = Primary
    assert by_uid["1"]["runs"] == 1
    # the sentinel event folds into the "0" bucket's security counts
    assert by_uid["0"]["sec_warning"] == 1
    assert by_uid["1"]["sec_high"] == 1
