"""Tests for ReportGenerator — Markdown assembly and repo delegation."""
import json
from unittest.mock import MagicMock

import pytest

from app.services.report_generator import ReportGenerator


def _score_row(job_id: str, workflow_id: str, overall: int = 85) -> dict:
    return {
        "id": f"sc-{job_id}",
        "workflow_run_id": workflow_id,
        "job_id": job_id,
        "resume_id": "res-001",
        "overall_score": overall,
        "score_json": json.dumps({
            "overall_score": overall,
            "technical_score": overall - 2,
            "architecture_score": overall + 1,
            "leadership_score": overall - 5,
            "domain_score": overall - 3,
            "match_summary": "Strong technical fit for a Staff Engineer role.",
            "strengths": ["Python", "Kubernetes"],
            "gaps": ["Limited fintech experience"],
        }),
    }


def _job_row(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "Staff Engineer",
        "company": "TechCorp",
        "url": "https://example.com/job",
    }


def _svc(
    scores=None, reviews=None, advice=None,
    tailoring=None, reports=None, jobs=None,
    workflow_id="wf-001", job_id="job-001",
):
    score_repo = MagicMock()
    score_repo.get_by_workflow_run.return_value = scores or []
    score_repo.get_by_job.return_value = [_score_row(job_id, workflow_id)]

    review_repo = MagicMock()
    review_repo.get_review_by_run_job.return_value = reviews

    advice_repo = MagicMock()
    advice_repo.get_advice_by_run_job.return_value = advice
    advice_repo.get_prep_by_run_job.return_value = None

    tailoring_repo = MagicMock()
    tailoring_repo.get_by_run_job.return_value = tailoring

    report_repo = MagicMock()
    job_repo = MagicMock()
    job_repo.get_by_id.return_value = jobs or _job_row(job_id)

    return ReportGenerator(
        score_repo=score_repo,
        review_repo=review_repo,
        advice_repo=advice_repo,
        tailoring_repo=tailoring_repo,
        report_repo=report_repo,
        job_repo=job_repo,
    )


# ── generate_run_summary ──────────────────────────────────────────────────────

def test_generate_run_summary_contains_workflow_id():
    svc = _svc()
    result = svc.generate_run_summary("wf-test-001")
    assert "wf-test-001" in result


def test_generate_run_summary_includes_job_table():
    svc = _svc(scores=[_score_row("job-001", "wf-001")])
    result = svc.generate_run_summary("wf-001")
    assert "Jobs Scored" in result
    assert "Staff Engineer" in result


def test_generate_run_summary_no_scores_still_returns_string():
    svc = _svc(scores=[])
    result = svc.generate_run_summary("wf-001")
    assert isinstance(result, str)
    assert "wf-001" in result


def test_generate_run_summary_saves_to_report_repo():
    score_repo = MagicMock()
    score_repo.get_by_workflow_run.return_value = []
    score_repo.get_by_job.return_value = []
    report_repo = MagicMock()
    job_repo = MagicMock()
    job_repo.get_by_id.return_value = None

    svc = ReportGenerator(
        score_repo=score_repo,
        review_repo=MagicMock(),
        advice_repo=MagicMock(),
        tailoring_repo=MagicMock(),
        report_repo=report_repo,
        job_repo=job_repo,
    )
    svc.generate_run_summary("wf-001")
    report_repo.create.assert_called_once()


# ── generate_job_report ───────────────────────────────────────────────────────

def test_generate_job_report_includes_score_dimensions():
    svc = _svc(scores=[_score_row("job-001", "wf-001")])
    result = svc.generate_job_report("wf-001", "job-001")
    assert "Overall" in result
    assert "Technical" in result
    assert "Architecture" in result
    assert "Leadership" in result


def test_generate_job_report_includes_match_summary():
    svc = _svc(scores=[_score_row("job-001", "wf-001")])
    result = svc.generate_job_report("wf-001", "job-001")
    assert "Strong technical fit" in result


def test_generate_job_report_includes_strengths_and_gaps():
    svc = _svc(scores=[_score_row("job-001", "wf-001")])
    result = svc.generate_job_report("wf-001", "job-001")
    assert "Python" in result
    assert "Limited fintech" in result


def test_generate_job_report_includes_review_when_present():
    review = {
        "review_json": json.dumps({
            "overall_fit_summary": "Resume undersells architecture impact.",
            "critical_gaps": ["No published RFCs"],
            "resume_only_gaps": [],
            "career_gaps_observed": [],
        })
    }
    svc = _svc(scores=[_score_row("job-001", "wf-001")], reviews=review)
    result = svc.generate_job_report("wf-001", "job-001")
    assert "Resume Analysis" in result
    assert "undersells" in result


def test_generate_job_report_omits_tailoring_section_when_absent():
    svc = _svc(scores=[_score_row("job-001", "wf-001")], tailoring=None)
    result = svc.generate_job_report("wf-001", "job-001")
    assert "Tailoring" not in result


def test_generate_job_report_includes_approved_tailoring():
    tailoring = {
        "approved": 1,
        "tailored_json": json.dumps({
            "experience_bullet_suggestions": [
                {"original_text": "Maintained Kafka.", "suggested_text": "Operated 12-broker Kafka cluster."}
            ]
        }),
    }
    svc = _svc(scores=[_score_row("job-001", "wf-001")], tailoring=tailoring)
    result = svc.generate_job_report("wf-001", "job-001")
    assert "Tailoring" in result
    assert "Kafka" in result


def test_generate_job_report_no_data_returns_empty():
    score_repo = MagicMock()
    score_repo.get_by_job.return_value = []
    job_repo = MagicMock()
    job_repo.get_by_id.return_value = None

    svc = ReportGenerator(
        score_repo=score_repo,
        review_repo=MagicMock(),
        advice_repo=MagicMock(),
        tailoring_repo=MagicMock(),
        report_repo=MagicMock(),
        job_repo=job_repo,
    )
    result = svc.generate_job_report("wf-001", "job-nonexistent")
    assert result == ""
