# models/filters.py
# ─────────────────────────────────────────────────────────────────────────────
# Shared pre-filter lists used by both the Adzuna scraper (to skip API noise
# before storing) and the ScoringAgent (to skip Claude calls for all sources).
#
# Keeping them in one place prevents drift between the two gatekeeping layers.
# ─────────────────────────────────────────────────────────────────────────────

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
