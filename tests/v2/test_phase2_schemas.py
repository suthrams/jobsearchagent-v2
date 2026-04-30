"""Tests for Phase 2 data schemas: JobPosting and ResumeProfile."""
import pytest
from pydantic import ValidationError

from app.schemas.job_posting import JobPosting, JobSource, SalaryInfo, WorkMode
from app.schemas.resume_profile import (
    CertificationEntry,
    EducationEntry,
    ExperienceEntry,
    ResumeProfile,
)

NOW = "2026-04-29T12:00:00.000Z"


# ── JobPosting ────────────────────────────────────────────────────────────────

def _valid_posting(**overrides) -> dict:
    base = dict(
        job_id="jp-001",
        workflow_id="wf-001",
        url="https://example.com/job/1",
        source=JobSource.LINKEDIN,
        title="Staff Engineer",
        company="Acme Corp",
        found_at=NOW,
    )
    base.update(overrides)
    return base


def test_job_posting_valid():
    jp = JobPosting(**_valid_posting())
    assert jp.job_id == "jp-001"
    assert jp.source == JobSource.LINKEDIN
    assert jp.work_mode == WorkMode.UNKNOWN


def test_job_posting_work_mode_defaults_to_unknown():
    jp = JobPosting(**_valid_posting())
    assert jp.work_mode == WorkMode.UNKNOWN


def test_job_posting_explicit_work_mode():
    jp = JobPosting(**_valid_posting(work_mode=WorkMode.REMOTE))
    assert jp.work_mode == WorkMode.REMOTE


def test_job_posting_missing_required_fields():
    with pytest.raises(ValidationError):
        JobPosting(job_id="jp-001", workflow_id="wf-001")  # missing url, source, title, etc.


def test_job_posting_optional_fields_default_none():
    jp = JobPosting(**_valid_posting())
    assert jp.location is None
    assert jp.description is None
    assert jp.salary is None
    assert jp.posted_at is None


def test_job_posting_invalid_source():
    with pytest.raises(ValidationError):
        JobPosting(**_valid_posting(source="indeed"))


def test_salary_info_all_none():
    s = SalaryInfo()
    assert s.min_amount is None
    assert s.max_amount is None
    assert s.currency == "USD"


def test_salary_info_with_values():
    s = SalaryInfo(min_amount=150000, max_amount=200000, currency="USD")
    assert s.min_amount == 150000


def test_job_posting_with_salary():
    jp = JobPosting(**_valid_posting(salary=SalaryInfo(min_amount=150000)))
    assert jp.salary is not None
    assert jp.salary.min_amount == 150000


# ── ResumeProfile ─────────────────────────────────────────────────────────────

def _valid_profile(**overrides) -> dict:
    base = dict(
        resume_id="res-001",
        raw_text="John Doe\nSoftware Engineer\nSkills: Python, Kubernetes\n" * 5,
        parsed_at=NOW,
    )
    base.update(overrides)
    return base


def test_resume_profile_valid():
    rp = ResumeProfile(**_valid_profile())
    assert rp.resume_id == "res-001"
    assert rp.experience == []
    assert rp.skills == []


def test_resume_profile_empty_raw_text_rejected():
    with pytest.raises(ValidationError):
        ResumeProfile(**_valid_profile(raw_text=""))


def test_resume_profile_whitespace_only_raw_text_rejected():
    with pytest.raises(ValidationError):
        ResumeProfile(**_valid_profile(raw_text="   \n\t  "))


def test_resume_profile_optional_fields_default_none():
    rp = ResumeProfile(**_valid_profile())
    assert rp.name is None
    assert rp.email is None
    assert rp.headline is None


def test_experience_entry_current_role():
    e = ExperienceEntry(company="Acme", title="Staff Engineer", start_year=2020)
    assert e.end_year is None


def test_experience_entry_past_role():
    e = ExperienceEntry(company="Acme", title="SRE", start_year=2018, end_year=2022)
    assert e.end_year == 2022


def test_education_entry():
    edu = EducationEntry(institution="Georgia Tech", degree="B.S. Computer Science", year=2010)
    assert edu.year == 2010


def test_certification_entry_optional_fields():
    cert = CertificationEntry(name="AWS Solutions Architect")
    assert cert.issuer is None
    assert cert.year is None


def test_resume_profile_with_all_fields():
    rp = ResumeProfile(
        resume_id="res-002",
        raw_text="A" * 100,
        parsed_at=NOW,
        name="Jane Smith",
        email="jane@example.com",
        skills=["Python", "Kubernetes"],
        experience=[ExperienceEntry(company="DataCo", title="Principal Engineer", start_year=2019)],
        education=[EducationEntry(institution="MIT", degree="M.S. CS", year=2015)],
        certifications=[CertificationEntry(name="CKA", issuer="CNCF", year=2022)],
    )
    assert rp.name == "Jane Smith"
    assert len(rp.experience) == 1
    assert len(rp.skills) == 2
