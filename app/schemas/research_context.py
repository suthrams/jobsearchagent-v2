"""ResearchContext — structured output schema for the Research Agent.

Captures company and role signals gathered via bounded ReAct (max 2 steps).
research_steps stores observation summaries only — never raw chain-of-thought.

Field strictness (same pattern as ResumeReview / TailoredResumeDraft):
  Load-bearing: job_id, company_summary, role_context.
  Tolerant (default to empty list / 0): the four signal lists, research_steps,
  confidence. Haiku sometimes omits these on simpler queries.

Stringified-list coercion: Haiku occasionally emits a list[str] field as a
JSON-encoded STRING instead of a real list (observed: leadership_signals
arrived as '["Head of...", "CTO..."]'). The validator below parses these
back to real lists so the schema-repair retry isn't burned on a Haiku
emission quirk.
"""
import json

from pydantic import BaseModel, Field, field_validator


class ResearchStep(BaseModel):
    step_number: int
    tool_used: str
    observation_summary: str


class ResearchContext(BaseModel):
    # Load-bearing — required.
    job_id: str
    company_summary: str
    role_context: str
    # Tolerant — default to empty list. Real signal can still be ranked by
    # the scoring agent even if research found nothing in this category.
    technology_signals: list[str] = Field(default_factory=list)
    leadership_signals: list[str] = Field(default_factory=list)
    domain_signals: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    research_steps: list[ResearchStep] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)

    @field_validator(
        "technology_signals", "leadership_signals", "domain_signals", "risk_flags",
        mode="before",
    )
    @classmethod
    def _coerce_stringified_list(cls, v):
        """Haiku quirk: list[str] sometimes arrives as a JSON-encoded string.

        Examples seen in production:
          '["Head of Engineering title indicates leadership", "CTO or VP Engineering"]'
        That's a single str, not a list. Parse it back to a real list.

        Falls back to wrapping a non-JSON string in a one-item list (best effort).
        """
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s:
            return []
        # JSON-encoded list -> real list
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except json.JSONDecodeError:
                pass
        # Single string -> one-item list (worst-case best effort)
        return [s]
