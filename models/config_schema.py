# models/config_schema.py
# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models that mirror config/config.yaml.
# config.yaml is loaded once at startup and validated against these models.
# This catches missing keys and type errors before any API calls are made.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ─── Search ───────────────────────────────────────────────────────────────────

class YearsOfExperience(BaseModel):
    """
    Optional filter to skip jobs that require too little or too much experience.
    Both bounds are optional — set only the ones you care about.
    """
    min: Optional[int] = Field(None, description="Minimum years of experience required by the job")
    max: Optional[int] = Field(None, description="Maximum years of experience required by the job")


class SearchConfig(BaseModel):
    """
    Controls what kinds of jobs are pulled in by the scrapers.
    titles and locations are the minimum required fields.
    keywords and years_of_experience are optional filters.
    """
    titles:                list[str]                    = Field(..., description="Job titles to search for")
    locations:             list[str]                    = Field(..., description="Target locations, e.g. ['Atlanta, GA', 'Remote']")
    work_mode:             list[str]                    = Field(default_factory=list, description="Accepted work modes: remote, hybrid, onsite")
    keywords:              Optional[list[str]]          = Field(None, description="Optional technology keywords to filter by, e.g. gcp, kubernetes")
    years_of_experience:   Optional[YearsOfExperience]  = Field(None, description="Optional experience range filter")


# ─── Salary ───────────────────────────────────────────────────────────────────

class SalaryConfig(BaseModel):
    """
    Optional salary preferences.
    When ignore_if_missing is True, jobs without salary data are not penalized.
    Claude uses min_desired to flag roles that are likely underpaying.
    """
    min_desired:      int   = Field(150000, description="Minimum desired salary in the given currency")
    currency:         str   = Field("USD",  description="Currency code, e.g. USD")
    ignore_if_missing: bool = Field(True,   description="Do not penalize jobs that omit salary information")


# ─── Career tracks ────────────────────────────────────────────────────────────

class TracksConfig(BaseModel):
    """
    Enables or disables each career track.
    Only enabled tracks are scored — disabled tracks are skipped to save API costs.
    - ic         : Individual Contributor — Senior / Staff / Principal Engineer
    - architect  : Solutions Architect / Principal Architect
    - management : Senior Manager / Director / Head of Engineering / VP
    """
    ic:         bool = Field(True,  description="Score jobs against the IC engineering track")
    architect:  bool = Field(True,  description="Score jobs against the architect track")
    management: bool = Field(True,  description="Score jobs against the management track")


# ─── Claude ───────────────────────────────────────────────────────────────────

class MaxTokensConfig(BaseModel):
    """
    Token limits per Claude operation.
    Keeps API costs predictable — tailoring gets more tokens because
    it produces a full rewritten resume section, not just a JSON score.
    """
    resume_parsing:   int = Field(1000, description="Max tokens for resume parsing")
    job_scoring:      int = Field(3500, description="Max tokens for job scoring (covers up to 10 jobs per batch)")
    resume_tailoring: int = Field(2000, description="Max tokens for resume tailoring")


class TemperatureConfig(BaseModel):
    """
    Temperature per Claude operation.
    Lower temperature = more deterministic JSON output.
    Tailoring gets a slightly higher temperature for more natural language.
    """
    resume_parsing:   float = Field(0.1, description="Temperature for resume parsing")
    job_scoring:      float = Field(0.1, description="Temperature for job scoring")
    resume_tailoring: float = Field(0.3, description="Temperature for resume tailoring")


class ClaudeConfig(BaseModel):
    """
    All settings related to Claude API calls.
    Model name, token limits, and temperature are all configurable here
    so you can tune cost vs quality without touching code.
    """
    model:      str             = Field("claude-sonnet-4-6", description="Claude model to use for all operations")
    max_tokens: MaxTokensConfig = Field(default_factory=MaxTokensConfig, description="Token limits per operation")
    temperature: TemperatureConfig = Field(default_factory=TemperatureConfig, description="Temperature per operation")


# ─── Scrapers ─────────────────────────────────────────────────────────────────

class LinkedInConfig(BaseModel):
    """
    LinkedIn is handled manually — you paste URLs into inbox_file, one per line.
    The scraper reads this file, fetches each URL, and creates Job objects.
    """
    inbox_file: str = Field("inbox/linkedin.txt", description="Path to file containing LinkedIn URLs to process")


class AdzunaConfig(BaseModel):
    """
    Adzuna API scraper configuration.
    Replaces both Indeed and Glassdoor — Adzuna aggregates from both and more.
    Requires ADZUNA_APP_ID and ADZUNA_APP_KEY in your .env file.
    - country          : ISO country code, 'us' for United States
    - keywords         : Search terms — one API call per keyword
    - locations        : List of cities/states to search, e.g. ['Atlanta, GA', 'Houston, TX']
    - Local search keywords come from search.titles (AppConfig), not from this model.
    - radius_km        : Search radius in kilometres around the location
    - results_per_page : Number of results per keyword (max 50 on free tier)
    """

    enabled: bool = Field(True, description="Whether to run the Adzuna scraper")
    country: str = Field("us", description="ISO country code for the Adzuna endpoint")
    locations: list[str] = Field(default_factory=list, description="Cities/states to search, e.g. ['Atlanta, GA', 'Houston, TX']")
    radius_km: int = Field(80, description="Search radius in kilometres")
    results_per_page: int = Field(10, description="Results per keyword per call (max 50)")
    remote_keywords: list[str] = Field(default_factory=list, description="Subset of titles for US-wide remote search. Kept separate for quota control — remote adds one call per entry.")


class LaddersConfig(BaseModel):
    """
    Ladders focuses on $100k+ roles — high signal-to-noise for senior jobs.
    keywords are used to build the search URL.
    """
    enabled:  bool      = Field(True, description="Whether to run the Ladders scraper")
    keywords: list[str] = Field(default_factory=list, description="Search keywords for Ladders")


class ScrapersConfig(BaseModel):
    """
    Top-level container for all scraper configurations.
    Each scraper can be enabled or disabled independently.
    """
    linkedin: LinkedInConfig = Field(default_factory=LinkedInConfig,  description="LinkedIn manual intake settings")
    adzuna:   AdzunaConfig   = Field(default_factory=AdzunaConfig,    description="Adzuna API scraper settings")
    ladders:  LaddersConfig  = Field(default_factory=LaddersConfig,   description="Ladders scraper settings")

# ─── Storage ──────────────────────────────────────────────────────────────────

class StorageConfig(BaseModel):
    """
    File system paths for persistent storage.
    All paths are relative to the project root.
    - database            : SQLite database file for all job records
    - tailored_resumes_dir: where Claude-tailored resumes are saved as text files
    - logs_dir            : where run logs are written
    """
    database:             str = Field("data/jobs.db",      description="Path to SQLite database file")
    tailored_resumes_dir: str = Field("output/resumes",    description="Directory for tailored resume output files")
    logs_dir:             str = Field("output/logs",       description="Directory for run log files")


# ─── Staleness ────────────────────────────────────────────────────────────────

class StalenessConfig(BaseModel):
    """
    Controls how old a job posting can be before it is skipped.
    Jobs older than max_days are not scored — saves API costs on dead listings.
    Jobs without a posted_at date are always scored (benefit of the doubt).
    """
    max_days: int = Field(30, description="Maximum age of a job posting in days before it is considered stale")


# ─── Root config ──────────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    """
    Root configuration object. Loaded from config/config.yaml at startup.
    Every section of config.yaml maps to a typed sub-model here.
    If config.yaml is missing required fields, Pydantic raises a clear error
    before any scraping or API calls begin.
    """
    search:    SearchConfig   = Field(...,                          description="Job search preferences")
    salary:    SalaryConfig   = Field(default_factory=SalaryConfig, description="Salary preferences")
    tracks:    TracksConfig   = Field(default_factory=TracksConfig, description="Career track enable/disable flags")
    claude:    ClaudeConfig   = Field(default_factory=ClaudeConfig, description="Claude API settings")
    scrapers:  ScrapersConfig = Field(default_factory=ScrapersConfig, description="Scraper settings per source")
    storage:   StorageConfig  = Field(default_factory=StorageConfig, description="File system storage paths")
    staleness: StalenessConfig = Field(default_factory=StalenessConfig, description="Stale job filtering settings")
