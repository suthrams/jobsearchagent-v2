"""v2 parsed resume schema — produced by ResumeParser, consumed by all agents."""
from pydantic import BaseModel, Field, field_validator


class ExperienceEntry(BaseModel):
    company: str
    title: str
    start_year: int
    end_year: int | None = None  # None = current role
    description: str | None = None
    technologies: list[str] = []


class EducationEntry(BaseModel):
    institution: str
    degree: str
    year: int | None = None
    # ADR-067: preserve resume content the v1 schema had no slot for.
    gpa: str | None = None                       # as-written, e.g. "3.9/4.0", "First Class"
    honors: list[str] = Field(default_factory=list)  # free-text awards / dean's list / etc.


class CertificationEntry(BaseModel):
    name: str
    issuer: str | None = None
    year: int | None = None


class SkillGroup(BaseModel):
    """ADR-067: a categorised view of skills, when the source resume listed
    them under category headings (e.g. "Security & Monitoring: SIEM, ...").

    The flat `ResumeProfile.skills` list stays as the canonical input for the
    Scoring Agent and the keyword filters; `skill_groups` is the structural
    overlay used by the resume renderer to preserve the source's grouping.
    """
    category: str
    skills: list[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    """Parsed resume. raw_text is mandatory — it is the Fidelity Reviewer's source of truth."""

    resume_id: str
    file_name: str | None = None
    raw_text: str
    name: str | None = None
    headline: str | None = None
    email: str | None = None
    location: str | None = None
    summary: str | None = None
    experience: list[ExperienceEntry] = []
    skills: list[str] = []
    # ADR-067: optional categorised view. When populated, the flat `skills`
    # list above is the union of all groups' skills. When empty, downstream
    # consumers fall back to the flat list.
    skill_groups: list[SkillGroup] = Field(default_factory=list)
    education: list[EducationEntry] = []
    certifications: list[CertificationEntry] = []
    parsed_at: str  # ISO 8601 UTC

    @field_validator("raw_text")
    @classmethod
    def raw_text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("raw_text must not be empty — ResumeParser failed to extract text")
        return v
