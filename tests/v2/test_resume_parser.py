"""Tests for ResumeParser — heuristic-only mode (enhance_fn=None) and caching."""
from unittest.mock import MagicMock

import pytest

from app.schemas.resume_profile import ResumeProfile
from app.services.resume_parser import ResumeParseError, ResumeParser


_RESUME_TEXT = """
John Doe
john.doe@example.com | Remote

SUMMARY
Experienced Staff Engineer with 10 years building distributed systems.

EXPERIENCE
TechCorp — Staff Engineer (2020–Present)
Led platform migration to GCP. Kubernetes, Python, Kafka.

SKILLS
Python, Kubernetes, GCP, Kafka, Go

EDUCATION
Georgia Tech — B.S. Computer Science, 2012

CERTIFICATIONS
Certified Kubernetes Administrator (CKA), CNCF, 2022
""" * 3  # make it long enough to pass the 50-char minimum


def _mock_repo(cached=None):
    repo = MagicMock()
    import json
    repo.get_by_raw_text_hash.return_value = (
        {"parsed_profile_json": json.dumps(cached.model_dump())} if cached else None
    )
    return repo


def _parser(repo=None, enhance_fn=None):
    return ResumeParser(
        resume_repository=repo or _mock_repo(),
        enhance_fn=enhance_fn,
    )


# ── Basic parse ───────────────────────────────────────────────────────────────

def test_parse_text_returns_resume_profile():
    p = _parser()
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    assert isinstance(result, ResumeProfile)


def test_parse_text_sets_raw_text():
    p = _parser()
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    assert result.raw_text == _RESUME_TEXT


def test_parse_text_assigns_uuid_resume_id():
    p = _parser()
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    assert len(result.resume_id) == 36


def test_parse_text_sets_parsed_at():
    p = _parser()
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    assert result.parsed_at.endswith("Z")


def test_parse_text_extracts_email():
    p = _parser()
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    assert result.email == "john.doe@example.com"


def test_parse_text_extracts_skills():
    p = _parser()
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    assert "Python" in result.skills or "python" in [s.lower() for s in result.skills]


# ── Error cases ───────────────────────────────────────────────────────────────

def test_parse_text_empty_raises():
    p = _parser()
    with pytest.raises(ResumeParseError, match="empty PDF"):
        p.parse_text("", "resume.pdf")


def test_parse_text_too_short_raises():
    p = _parser()
    with pytest.raises(ResumeParseError):
        p.parse_text("short", "resume.pdf")


def test_parse_text_no_sections_returns_profile_with_raw_text():
    long_text = "No recognizable sections here. " * 10
    p = _parser()
    result = p.parse_text(long_text, "resume.pdf")
    assert isinstance(result, ResumeProfile)
    assert result.raw_text == long_text


# ── Caching ───────────────────────────────────────────────────────────────────

def test_cache_hit_skips_enhance_fn():
    cached_profile = ResumeProfile(
        resume_id="cached-001",
        raw_text=_RESUME_TEXT,
        parsed_at="2026-01-01T00:00:00.000Z",
    )
    repo = _mock_repo(cached=cached_profile)
    enhance_fn = MagicMock(return_value={})
    p = ResumeParser(resume_repository=repo, enhance_fn=enhance_fn)
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    enhance_fn.assert_not_called()
    assert result.resume_id == "cached-001"


def test_cache_miss_calls_enhance_fn():
    repo = _mock_repo(cached=None)
    enhance_fn = MagicMock(return_value={"name": "Jane Smith", "skills": ["Python"]})
    p = ResumeParser(resume_repository=repo, enhance_fn=enhance_fn)
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    enhance_fn.assert_called_once()
    assert result.name == "Jane Smith"


def test_cache_miss_saves_to_repo():
    repo = _mock_repo(cached=None)
    p = _parser(repo=repo)
    p.parse_text(_RESUME_TEXT, "resume.pdf")
    repo.create.assert_called_once()


def test_cache_hit_stable_resume_id():
    cached_profile = ResumeProfile(
        resume_id="stable-id-999",
        raw_text=_RESUME_TEXT,
        parsed_at="2026-01-01T00:00:00.000Z",
    )
    repo = _mock_repo(cached=cached_profile)
    p = ResumeParser(resume_repository=repo)
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    assert result.resume_id == "stable-id-999"


# ── enhance_fn error handling ────────────────────────────────────────────────

def test_enhance_fn_failure_raises_parse_error():
    repo = _mock_repo(cached=None)
    enhance_fn = MagicMock(side_effect=ValueError("Claude returned invalid JSON"))
    p = ResumeParser(resume_repository=repo, enhance_fn=enhance_fn)
    with pytest.raises(ResumeParseError, match="Claude enhancement failed"):
        p.parse_text(_RESUME_TEXT, "resume.pdf")


def test_raw_text_always_set_even_with_enhance_fn():
    repo = _mock_repo(cached=None)
    enhance_fn = MagicMock(return_value={"raw_text": "REPLACED", "name": "Override"})
    p = ResumeParser(resume_repository=repo, enhance_fn=enhance_fn)
    result = p.parse_text(_RESUME_TEXT, "resume.pdf")
    # raw_text must always be the original extracted text, never Claude's output
    assert result.raw_text == _RESUME_TEXT
