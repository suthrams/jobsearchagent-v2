"""API request schemas — Pydantic v2 models for all incoming HTTP request bodies."""
from __future__ import annotations

from pydantic import BaseModel, Field


class StartWorkflowRequest(BaseModel):
    resume_id: str
    search_criteria: dict
    workflow_type: str = "full_career_review"
    effective_config: dict = Field(default_factory=dict)
    # User-supplied job posting URLs (LinkedIn, company career pages, etc.)
    # Scraped per run by CustomUrlScraper, additive to standard scrapers.
    custom_urls: list[str] = Field(default_factory=list, max_length=25)
    # Per-workflow agent override hook (ADR-058). When non-empty, this map
    # records `{agent_name: {provider, model}}` into the run's persisted
    # state for reproducibility. NOTE: Phase 1 of ADR-058 records the override
    # in the workflow snapshot but does NOT swap the runtime provider — agents
    # in this run still resolve through the global registry assignment. Phase 9
    # closes that gap by introducing per-run agent rebuild or lazy provider
    # resolution. The server returns a warning in the response when overrides
    # are present.
    agent_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)
