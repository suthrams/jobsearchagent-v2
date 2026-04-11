# tests/test_filters.py
# ─────────────────────────────────────────────────────────────────────────────
# Tests for models/filters.py — the shared title and description filter lists.
#
# Bugs these catch:
#   - Noisy titles slipping through (property manager, leasing, etc.)
#   - Relevant titles being wrongly excluded
#   - Overly broad description keywords matching non-tech roles
#   - Exclusion list drift between scraper and scoring agent
# ─────────────────────────────────────────────────────────────────────────────

import pytest
from models.filters import (
    EXCLUDED_TITLE_KEYWORDS,
    RELEVANT_TITLE_KEYWORDS,
    TECH_DESCRIPTION_KEYWORDS,
)


def _is_relevant(title: str) -> bool:
    """Mirrors AdzunaScraper._is_relevant_title logic."""
    t = title.lower()
    if not any(kw in t for kw in RELEVANT_TITLE_KEYWORDS):
        return False
    if any(kw in t for kw in EXCLUDED_TITLE_KEYWORDS):
        return False
    return True


def _has_tech_desc(description: str) -> bool:
    """Mirrors ScoringAgent._has_tech_description logic."""
    d = description.lower()
    return any(kw in d for kw in TECH_DESCRIPTION_KEYWORDS)


# ─── Titles that SHOULD be included ──────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Software Architect",
    "Principal Engineer",
    "Senior Software Engineer",
    "Director of Engineering",
    "Head of Engineering",
    "VP of Engineering",
    "Solutions Architect",
    "Principal Architect",
    "Cloud Architect",
    "Senior Manager, Engineering",
    "Staff Engineer",
    "Platform Lead",
    "IoT Systems Architect",
    "Senior AI Architect",
    "Senior Cloud Security Architect",
    "Manager, Application Engineering & Integration",
])
def test_relevant_title_accepted(title):
    assert _is_relevant(title), f"Expected '{title}' to pass filter but it was excluded"


# ─── Titles that SHOULD be excluded (noise) ──────────────────────────────────

@pytest.mark.parametrize("title", [
    # From the screenshot — confirmed noise
    "Assistant Property Manager",
    "Architect and Design Specification Representative",
    "Assistant Community Manager",
    "Fundraising Manager",
    "Sales (Leasing) Manager",
    "Leasing Manager - Apartments",
    "Manager Medical Transcription Operations",
    "Associate Project Manager",
    "Sr. Program Manager - REMOTE",
    "AI Project Manager (IT Deployment)",
    # Other common non-tech noise
    "Branch Manager",
    "Office Manager",
    "Electrical Engineer",
    "Civil Engineer",
    "Structural Engineer",
    "Mechanical Engineer",
    "Sales Engineer",
    "Account Manager",
    "Account Executive",
    "Java Developer",
    "Intern, AI & Automation Engineering",
    "Hotel Operations Lead",
    "HVAC Engineer",
])
def test_noise_title_excluded(title):
    assert not _is_relevant(title), f"Expected '{title}' to be excluded but it passed filter"


# ─── Descriptions that SHOULD trigger tech gate ──────────────────────────────

@pytest.mark.parametrize("description", [
    "Lead a team of software engineers building cloud-native microservices on AWS.",
    "Design distributed systems using Python, Kubernetes, and Terraform.",
    "Drive digital transformation and manage the engineering team.",
    "Architect IoT solutions using MQTT, edge computing, and device management.",
    "Responsible for data engineering pipelines and machine learning platforms.",
])
def test_tech_description_accepted(description):
    assert _has_tech_desc(description), f"Expected description to pass tech gate: '{description[:60]}...'"


# ─── Descriptions that SHOULD NOT trigger tech gate ──────────────────────────

@pytest.mark.parametrize("description", [
    "Manage leasing operations and resident relations at luxury apartments.",
    "Oversee fundraising campaigns and donor engagement programs.",
    "Responsible for HVAC installation and building maintenance.",
    "Coordinate medical transcription and clinical documentation workflows.",
    "Manage substation construction projects in the Southeast region.",
])
def test_non_tech_description_rejected(description):
    assert not _has_tech_desc(description), f"Expected description to fail tech gate: '{description[:60]}...'"


# ─── Both filters imported from one place ─────────────────────────────────────

def test_filters_imported_from_shared_module():
    """
    Ensures both scrapers/adzuna.py and agents/scoring_agent.py import filters
    from models.filters — not from local copies. If they define their own lists
    this test catches the drift.
    """
    import scrapers.adzuna as adzuna_mod
    import agents.scoring_agent as scoring_mod
    from models import filters as filters_mod

    assert adzuna_mod.EXCLUDED_TITLE_KEYWORDS is filters_mod.EXCLUDED_TITLE_KEYWORDS, \
        "adzuna.py must import EXCLUDED_TITLE_KEYWORDS from models.filters, not define its own"

    assert scoring_mod.EXCLUDED_TITLE_KEYWORDS is filters_mod.EXCLUDED_TITLE_KEYWORDS, \
        "scoring_agent.py must import EXCLUDED_TITLE_KEYWORDS from models.filters, not define its own"

    assert scoring_mod.TECH_DESCRIPTION_KEYWORDS is filters_mod.TECH_DESCRIPTION_KEYWORDS, \
        "scoring_agent.py must import TECH_DESCRIPTION_KEYWORDS from models.filters, not define its own"
