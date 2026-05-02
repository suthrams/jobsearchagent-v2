"""Integration smoke test — real ClaudeProvider against the Anthropic API.

Skipped automatically when ANTHROPIC_API_KEY is not set (CI / no-creds environments).
Run manually:
    pytest tests/v2/test_live_provider.py -m integration -s

Marks: integration
Cost: ~1 small API call per test function (research_agent prompt, haiku model).
"""
from __future__ import annotations

import os

import pytest

from app.providers.claude_provider import ClaudeProvider, make_resume_enhance_fn
from app.providers.prompt_loader import PromptLoader
from app.schemas.research_context import ResearchContext

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def haiku_provider():
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live provider tests")
    loader = PromptLoader()
    return ClaudeProvider(loader, model_name="claude-haiku-4-5-20251001")


def test_research_agent_returns_valid_schema(haiku_provider):
    """ClaudeProvider.complete() with research_agent returns a valid ResearchContext."""
    context = {
        "job_id": "test-job-live-001",
        "job_title": "Senior Software Engineer",
        "company": "Acme Corp",
        "source_url": "https://example.com/jobs/123",
        "job_description": (
            "We are looking for a Senior Software Engineer with 5+ years of Python "
            "experience to join our platform team. You will design scalable APIs, "
            "lead technical initiatives, and mentor junior engineers."
        ),
    }
    result = haiku_provider.complete("research_agent", context, ResearchContext)
    validated = ResearchContext(**result)

    assert validated.job_id == "test-job-live-001"
    assert isinstance(validated.company_summary, str) and len(validated.company_summary) > 0
    assert isinstance(validated.technology_signals, list)
    assert 0 <= validated.confidence <= 100


def test_enhance_fn_returns_dict(haiku_provider):
    """make_resume_enhance_fn returns a callable that outputs a plain dict."""
    enhance_fn = make_resume_enhance_fn(haiku_provider)
    raw_text = (
        "Jane Doe\njane@example.com\n\n"
        "EXPERIENCE\nSenior Engineer at Acme (2020-2024)\n- Built Python microservices\n\n"
        "SKILLS\nPython, SQL, AWS"
    )
    heuristic_fields = {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "skills": ["Python", "SQL", "AWS"],
    }
    result = enhance_fn(raw_text, heuristic_fields)

    assert isinstance(result, dict)
    # The enhance fn should at minimum echo back or enrich the name
    assert "full_name" in result or "name" in result or len(result) > 0
