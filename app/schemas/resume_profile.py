"""v2 parsed resume schema — produced by ResumeParser, consumed by all agents."""
from pydantic import BaseModel, field_validator


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


class CertificationEntry(BaseModel):
    name: str
    issuer: str | None = None
    year: int | None = None


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
    education: list[EducationEntry] = []
    certifications: list[CertificationEntry] = []
    parsed_at: str  # ISO 8601 UTC

    @field_validator("raw_text")
    @classmethod
    def raw_text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("raw_text must not be empty — ResumeParser failed to extract text")
        return v
