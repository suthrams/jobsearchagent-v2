# models/filters.py
# ─────────────────────────────────────────────────────────────────────────────
# Shared pre-filter lists used by both the Adzuna scraper (to skip API noise
# before storing) and the ScoringAgent (to skip Claude calls for all sources).
#
# Keeping them in one place prevents drift between the two gatekeeping layers.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from typing import Optional

# Job title must contain at least one of these to be considered relevant.
# Broad enough to catch all target roles; noise is trimmed by EXCLUDED_TITLE_KEYWORDS.
RELEVANT_TITLE_KEYWORDS: list[str] = [
    "engineer",
    "architect",
    "director",
    "manager",
    "principal",
    "staff",
    "lead",
    "head of",
    "vp",
    "vice president",
    "software",
    "platform",
    "cloud",
    "devops",
    "sre",
    "infrastructure",
    "solutions",
    "developer",
    # IoT / connected devices
    "iot",
    "embedded",
    "connected devices",
    "edge computing",
]

# Any title containing one of these is dropped even if it matched RELEVANT_TITLE_KEYWORDS.
# Order: most specific phrases first so substring matches are unambiguous.
EXCLUDED_TITLE_KEYWORDS: list[str] = [
    # Sales / business development
    "presales",
    "pre-sales",
    "pre sales",
    "sales manager",
    "sales representative",
    "sales engineer",
    "account manager",
    "account executive",
    "business development",
    # Non-tech management
    "property manager",
    "community manager",
    "leasing manager",
    "leasing",
    "project manager",
    "program manager",
    "office manager",
    "store manager",
    "branch manager",
    "operations manager",
    "business systems analyst",
    "fundraising",
    "transcription",
    "substation",
    # Non-software engineering & architecture disciplines
    "electrical engineer",
    "mechanical engineer",
    "civil engineer",
    "structural engineer",
    "chemical engineer",
    "landscape architect",
    "design specification",     # construction / building architect
    "facilities engineer",
    "maintenance engineer",
    "hydraulic engineer",
    "process engineer",
    "department lead",
    # Hospitality / non-IT domains
    "hotel",
    "hvac",
    "medical",
    # Junior / student roles
    "intern",
    "internship",
    "associate engineer",
    # Language-specific (too narrow for this profile)
    "java developer",
    "java engineer",
]

# Job description must contain at least one of these for the job to be sent to Claude.
# Acts as a universal tech-domain gate — short Adzuna snippets always name the domain.
# Deliberately avoids broad words like "technology" or "technical" that appear in
# non-IT job descriptions (HR tech, biotech, medical technology, etc.).
TECH_DESCRIPTION_KEYWORDS: list[str] = [
    # Languages & runtimes
    "software", "python", "javascript", "typescript", ".net", "golang", "rust", "java",
    # Cloud & infra
    "cloud", "aws", "azure", "gcp", "kubernetes", "docker", "terraform", "ci/cd", "cicd",
    "infrastructure", "devops", "platform engineering",
    # APIs & architecture patterns
    "api", "microservice", "distributed system", "backend", "frontend", "full stack",
    "fullstack", "saas", "paas", "iaas", "application development",
    # Data & AI
    "data engineering", "data pipeline", "machine learning", "artificial intelligence",
    " ai ", "llm", "database",
    # Org / leadership (scoped tightly)
    "engineering team", "software engineer", "software development",
    "digital transformation",
    # IoT / embedded / edge
    "iot", "internet of things", "mqtt", "edge computing",
    "embedded", "connected devices", "industrial iot", "iiot",
    "device management", "telemetry", "firmware",
]

# ─── US state extraction ──────────────────────────────────────────────────────

_STATE_ABBREVS: frozenset[str] = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
})

# Sorted longest-first so multi-word states match before their substrings
# (e.g. "west virginia" before "virginia").
_STATE_NAMES: list[tuple[str, str]] = sorted(
    [
        ("alabama", "AL"), ("alaska", "AK"), ("arizona", "AZ"), ("arkansas", "AR"),
        ("california", "CA"), ("colorado", "CO"), ("connecticut", "CT"), ("delaware", "DE"),
        ("florida", "FL"), ("georgia", "GA"), ("hawaii", "HI"), ("idaho", "ID"),
        ("illinois", "IL"), ("indiana", "IN"), ("iowa", "IA"), ("kansas", "KS"),
        ("kentucky", "KY"), ("louisiana", "LA"), ("maine", "ME"), ("maryland", "MD"),
        ("massachusetts", "MA"), ("michigan", "MI"), ("minnesota", "MN"),
        ("mississippi", "MS"), ("missouri", "MO"), ("montana", "MT"), ("nebraska", "NE"),
        ("nevada", "NV"), ("new hampshire", "NH"), ("new jersey", "NJ"),
        ("new mexico", "NM"), ("new york", "NY"), ("north carolina", "NC"),
        ("north dakota", "ND"), ("ohio", "OH"), ("oklahoma", "OK"), ("oregon", "OR"),
        ("pennsylvania", "PA"), ("rhode island", "RI"), ("south carolina", "SC"),
        ("south dakota", "SD"), ("tennessee", "TN"), ("texas", "TX"), ("utah", "UT"),
        ("vermont", "VT"), ("virginia", "VA"), ("washington", "WA"),
        ("west virginia", "WV"), ("wisconsin", "WI"), ("wyoming", "WY"),
        ("district of columbia", "DC"),
    ],
    key=lambda x: len(x[0]),
    reverse=True,
)


def extract_us_state(location: Optional[str]) -> Optional[str]:
    """
    Returns a 2-letter US state abbreviation extracted from a location string,
    or None if no US state is recognisable.

    Handles common scraper formats:
      "Atlanta, GA"              → "GA"
      "Seattle, WA, United States" → "WA"
      "Austin, Texas"            → "TX"
      "Washington, DC"           → "DC"
      "Remote"                   → None
    """
    if not location:
        return None
    loc = location.strip()

    # Most common format from scrapers: ", XX" optionally followed by comma or end
    m = re.search(r",\s*([A-Z]{2})(?:\s*,|\s*$)", loc)
    if m and m.group(1) in _STATE_ABBREVS:
        return m.group(1)

    # Full state name (longest match first to avoid "virginia" matching "west virginia")
    lower = loc.lower()
    for name, abbrev in _STATE_NAMES:
        if re.search(r"\b" + re.escape(name) + r"\b", lower):
            return abbrev

    # Fallback: lone 2-letter uppercase token at end of string
    m = re.search(r"(?:^|,\s*)([A-Z]{2})$", loc)
    if m and m.group(1) in _STATE_ABBREVS:
        return m.group(1)

    return None
