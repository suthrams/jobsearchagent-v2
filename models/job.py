# models/job.py
# ─────────────────────────────────────────────────────────────────────────────
# Pydantic model representing a single job posting.
# This is the core data shape that flows through every part of the agent —
# scrapers create it, Claude scores it, and the database stores it.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────


class JobSource(str, Enum):
    """
    Identifies which scraper or intake method produced this job posting.
    Used to route display logic and track which sources yield the best results.
    - LINKEDIN  : manually pasted URLs from inbox/linkedin.txt
    - INDEED    : parsed from Indeed RSS feed
    - GLASSDOOR : parsed from Glassdoor RSS feed
    - LADDERS   : scraped from Ladders, which focuses on $100k+ roles
    - ADZUNA    : scraped from Adzuna job search API
    """

    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GLASSDOOR = "glassdoor"
    LADDERS = "ladders"
    ADZUNA = "adzuna"


class WorkMode(str, Enum):
    """
    Describes the physical working arrangement for the role.
    Parsed from the job posting text where available.
    - REMOTE : fully remote, no office attendance required
    - HYBRID : mix of remote and in-office
    - ONSITE : full-time in-office presence required
    """

    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class ApplicationStatus(str, Enum):
    """
    Tracks the lifecycle of a job posting through your application pipeline.
    Status transitions:
      NEW → SCORED → APPLIED → REJECTED
                             → OFFER
    - NEW      : job was just scraped, not yet scored by Claude
    - SCORED   : Claude has evaluated the job against all active tracks
    - APPLIED  : you have submitted an application for this role
    - REJECTED : either the company rejected you, or you chose to pass
    - OFFER    : company has extended an offer
    """

    NEW = "new"
    SCORED = "scored"
    APPLIED = "applied"
    REJECTED = "rejected"
    OFFER = "offer"


class CareerTrack(str, Enum):
    """
    The three career tracks that Claude scores each job against.
    Each track uses a different scoring prompt tuned to that role type.
    - IC         : Individual Contributor — Senior / Staff / Principal Engineer
    - ARCHITECT  : Solutions Architect / Principal Architect
    - MANAGEMENT : Senior Manager / Director / Head of Engineering / VP
    """

    IC = "ic"
    ARCHITECT = "architect"
    MANAGEMENT = "management"


# ─── Sub-models ───────────────────────────────────────────────────────────────


class SalaryRange(BaseModel):
    """
    Optional salary information extracted from a job posting.
    Many postings omit salary entirely — all fields are optional.
    When present, used by Claude during scoring to flag mismatches
    against your minimum desired salary set in config.yaml.
    """

    min: Optional[int] = Field(None, description="Minimum salary in the posting")
    max: Optional[int] = Field(None, description="Maximum salary in the posting")
    currency: str = Field("USD", description="Currency code, e.g. USD")


class TrackScore(BaseModel):
    """
    Claude's evaluation of a job posting for a single career track.
    Produced by the job scoring prompt and validated by Pydantic before storage.
    - score       : integer fit score from 0 to 100
    - summary     : one-sentence human-readable explanation of the score
    - recommended : whether Claude thinks you should apply for this track
    """

    score: int = Field(..., ge=0, le=100, description="Fit score 0–100")
    summary: str = Field(..., description="One-sentence explanation of the score")
    recommended: bool = Field(
        ..., description="Whether Claude recommends applying for this track"
    )


class TrackScores(BaseModel):
    """
    Container for scores across all three career tracks.
    Each track score starts as None and is populated after Claude runs.
    Only tracks enabled in config.yaml will be scored — the rest stay None.
    """

    ic: Optional[TrackScore] = None
    architect: Optional[TrackScore] = None
    management: Optional[TrackScore] = None


class BatchJobScore(BaseModel):
    """
    One item in Claude's batch scoring response.
    job_index matches the <job index="N"> tag in the prompt so scores
    can be mapped back to the correct Job even if Claude reorders them.
    """

    job_index: int
    ic: Optional[TrackScore] = None
    architect: Optional[TrackScore] = None
    management: Optional[TrackScore] = None


# ─── Main model ───────────────────────────────────────────────────────────────


class Job(BaseModel):
    """
    A single job posting. Created by a scraper, enriched by Claude,
    persisted to the database.

    Lifecycle:
      1. Scraper creates a Job with title, company, url, description, source
      2. Claude scores it — TrackScores are populated, status → SCORED
      3. You review it in the terminal summary
      4. If applying, resume tailoring runs and status → APPLIED
      5. Outcome is recorded — status → REJECTED or OFFER
    """

    # --- Identity ---
    # id is None until the job is inserted into the database
    id: Optional[int] = Field(None, description="Database row ID, set on insert")
    url: str = Field(..., description="Canonical URL of the job posting")
    source: JobSource = Field(..., description="Which scraper found this job")

    # --- Metadata ---
    # Core fields parsed from the posting header / listing card
    title: str = Field(..., description="Job title as listed in the posting")
    company: str = Field(..., description="Company name")
    location: Optional[str] = Field(
        None, description="Location string from the posting"
    )
    work_mode: Optional[WorkMode] = Field(None, description="Remote / hybrid / onsite")

    # --- Content ---
    description: Optional[str] = Field(
        None, description="Full job description text, stripped of HTML"
    )

    # --- Salary (optional) ---
    # None when the posting does not include salary information
    salary: Optional[SalaryRange] = Field(
        None, description="Salary range if present in posting"
    )

    # --- Scores (populated after Claude scoring) ---
    # Starts as empty TrackScores — all three tracks are None until scored
    scores: TrackScores = Field(
        default_factory=TrackScores, description="Per-track scores from Claude"
    )

    # --- Pipeline status ---
    # Starts as NEW, advances through the pipeline as you take action
    status: ApplicationStatus = Field(
        default=ApplicationStatus.NEW,
        description="Where this job sits in your application pipeline",
    )

    # --- Dates ---
    # posted_at  : when the company published the job — used to detect stale postings
    # expires_at : application deadline, rarely provided
    # found_at   : when our scraper picked it up — always set automatically
    # applied_at : when you submitted — set manually when status → APPLIED
    posted_at: Optional[datetime] = Field(
        None, description="When the job was posted, parsed from the listing"
    )
    expires_at: Optional[datetime] = Field(
        None, description="Application deadline if listed"
    )

    found_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="When the job was scraped",
    )

    applied_at: Optional[datetime] = Field(
        None, description="When you submitted the application"
    )

    @property
    def is_stale(self) -> bool:
        if self.posted_at is None:
            return False

        now = datetime.now(tz=timezone.utc)
        posted = self.posted_at
        # Make posted_at timezone-aware if it isn't already
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return (now - posted).days > 30

    class Config:
        """
        Pydantic model configuration.
        - populate_by_name : allows fields to be set by their Python name
        - use_enum_values  : serializes enums as their string values (e.g. "new" not ApplicationStatus.NEW)
                             this keeps the database and JSON output clean
        """

        populate_by_name = True
        use_enum_values = True
