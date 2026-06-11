"""BUG-010: a truncated Adzuna snippet must not let a cleared / above-entry job
through. The body-based clearance + experience filters miss the requirement when
it is past the ~500-char cut; these tests pin the title-based recovery.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.schemas.job_posting import JobPosting, JobSource, WorkMode
from app.repositories.database import utcnow_iso
from app.services.clearance_filter import requires_clearance
from app.services.seniority_filter import title_is_above_entry


# The exact posting that slipped through, reconstructed: the 500-char Adzuna snippet
# does NOT mention clearance or years, but the title states "Watch Floor" + "Mid".
_TMC_TITLE = "SOC Operations/Watch Floor Cybersecurity Analyst - Mid"
_TRUNCATED_BODY = ("Join our team supporting national security operations. You will "
                   "monitor sensor feeds, triage alerts, and coordinate response on a "
                   "24/7 watch floor. We value curiosity and a drive to learn. ") * 2
_TRUNCATED_BODY = _TRUNCATED_BODY[:500]  # mirror Adzuna's hard 500-char cut


# ── clearance: title catches it when the body is truncated ─────────────────────

def test_clearance_caught_by_title_when_body_truncated():
    # Body has no clearance phrase (it was truncated away) ...
    assert "clearance" not in _TRUNCATED_BODY.lower()
    assert requires_clearance(_TRUNCATED_BODY, None) is False
    # ... but the title's "Watch Floor" gov-SOC signal flags it.
    assert requires_clearance(_TRUNCATED_BODY, _TMC_TITLE) is True


def test_clearance_title_signals():
    for t in ["Cleared SOC Analyst", "SCIF Watch Officer",
              "Watch Floor Operator", "Cybersecurity Watch Officer"]:
        assert requires_clearance("", t) is True
    # Plain entry titles are not clearance-flagged.
    for t in ["SOC Analyst", "Junior Security Analyst", "Security Operations Analyst I"]:
        assert requires_clearance("", t) is False


def test_clearance_body_path_still_works():
    # Existing full-text detection (ATS) is unchanged.
    assert requires_clearance("Requires an active TS/SCI clearance.", "Analyst") is True
    assert requires_clearance("Must be able to obtain a security clearance.", None) is True
    # And precision holds: no false trip on benign phrases.
    assert requires_clearance("Clearance sale on our SaaS. Security+ a plus.", "Engineer") is False


# ── seniority: above-entry titles drop, entry titles stay ──────────────────────

def test_seniority_title_drops_mid_and_levels_keeps_entry():
    drop = [
        "SOC Operations/Watch Floor Cybersecurity Analyst - Mid",
        "Security Analyst II", "Cyber Analyst III", "Incident Responder IV",
        "Senior SOC Analyst", "Staff Security Engineer", "Principal Analyst",
        "Lead Detection Engineer", "Security Manager", "Tier 3 SOC Analyst",
        "Mid-Level Security Analyst", "Level 2 Analyst",
    ]
    for t in drop:
        assert title_is_above_entry(t) is True, f"should drop: {t}"

    keep = [
        "SOC Analyst", "Security Analyst I", "Tier 1 SOC Analyst",
        "Junior Security Analyst", "Associate Cybersecurity Analyst",
        "Cybersecurity Analyst", "Entry-Level Security Analyst",
        "Information Security Analyst",  # the genuine entry survivor
    ]
    for t in keep:
        assert title_is_above_entry(t) is False, f"should keep: {t}"


def test_seniority_word_boundaries_no_false_positives():
    # Boundary safety: these contain substrings of level terms but are entry/neutral.
    for t in ["Midwest Security Analyst", "Leadership Development Analyst Intern",
              "Cloud Security Analyst", "SecOps Analyst"]:
        assert title_is_above_entry(t) is False, f"false positive: {t}"


def test_seniority_safe_on_empty():
    assert title_is_above_entry(None) is False
    assert title_is_above_entry("") is False


# ── discovery wiring: exclude_senior applies the title drop across sources ──────

def _posting(title: str) -> JobPosting:
    return JobPosting(
        job_id=f"j-{abs(hash(title)) % 10000}", workflow_id="wf-bug010",
        url=f"https://adzuna.com/{abs(hash(title)) % 10000}", source=JobSource.ADZUNA,
        title=title, company="GovCo", work_mode=WorkMode.ONSITE,
        description="x" * 500,  # truncated snippet: no years, no clearance text
        found_at=utcnow_iso(),
    )


def test_discover_with_stats_drops_above_entry_titles_when_exclude_senior():
    from app.services.job_discovery_service import JobDiscoveryService

    repo = MagicMock()
    repo.url_excluded.return_value = False  # nothing pre-excluded -> dedup keeps all
    svc = JobDiscoveryService(repo, {"search": {"max_jobs": 50}}, scrapers=[])
    postings = [_posting("Security Analyst - Mid"), _posting("Senior SOC Analyst"),
                _posting("SOC Analyst I"), _posting("Cybersecurity Analyst")]
    svc.normalize = lambda job, wf: job  # already JobPosting

    kept, stats = svc.discover_with_stats(
        "wf-bug010", {"roles": ["analyst"]}, extra_scrapers=[
            type("S", (), {"scrape": lambda self: postings})()
        ], exclude_senior=True,
    )
    titles = {p.title for p in kept}
    assert "SOC Analyst I" in titles and "Cybersecurity Analyst" in titles
    assert "Security Analyst - Mid" not in titles
    assert "Senior SOC Analyst" not in titles
    assert stats["seniority_title_dropped"] == 2
