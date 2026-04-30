"""v2 canonical job representation — produced by JobDiscoveryService, consumed by all agents."""
from enum import Enum

from pydantic import BaseModel


class JobSource(str, Enum):
    LINKEDIN = "linkedin"
    ADZUNA = "adzuna"
    LADDERS = "ladders"
    MANUAL = "manual"


class WorkMode(str, Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class SalaryInfo(BaseModel):
    min_amount: int | None = None
    max_amount: int | None = None
    currency: str = "USD"


class JobPosting(BaseModel):
    """Normalised job posting. Every job in the system is converted to this before agents see it."""

    job_id: str
    workflow_id: str
    url: str
    source: JobSource
    title: str
    company: str
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    description: str | None = None
    salary: SalaryInfo | None = None
    found_at: str        # ISO 8601 UTC — when the scraper found it
    posted_at: str | None = None  # ISO 8601 UTC — when the company posted it, if parseable
