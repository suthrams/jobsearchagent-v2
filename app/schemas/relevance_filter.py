"""RelevanceFilterResult — structured output for the Relevance Filter Agent (ADR-079).

A single batched pass returns one verdict per discovered job: keep or drop, with a
PII-safe reason. The orchestrator's relevance_filter node hard-drops the jobs whose
verdict is keep=false before any scoring spend is paid, narrowing the discovered
set on the auto-scoring branch.

The seniority axis is judged RELATIVE to the profile's own target band, so the same
agent serves both ends: `too_senior` for an early-career profile, `too_junior` for a
senior one. `unrelated` covers the SUITABILITY axis: a role genuinely unsuitable for
the candidate (a different profession with no transferable overlap), NOT merely a
role outside the exact `target_roles`. Adjacent roles the candidate's career stage
could plausibly fill (e.g. IT / SOC / GRC / data for an entry cyber grad) are KEPT,
so the prefilter does not strand a candidate open to adjacent work. `none`
accompanies a keep.
"""
from typing import Literal

from pydantic import BaseModel, Field


class RelevanceVerdict(BaseModel):
    job_id: str
    keep: bool
    mismatch: Literal["none", "too_senior", "too_junior", "unrelated"] = "none"
    # PII-safe: about the POSTING (its level / domain), never the candidate's
    # identity or resume content (ADR-069 "summaries not raw content").
    reason: str = ""


class RelevanceFilterResult(BaseModel):
    verdicts: list[RelevanceVerdict] = Field(default_factory=list)
