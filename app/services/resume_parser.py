"""ResumeParser — two-phase PDF parsing: heuristic extraction + optional Claude enhancement.

Phase 1 (always): pdfminer.six text extraction + heuristic section detection.
Phase 2 (optional): enhance_fn (Claude) fills structured fields from raw_text.

SHA-256 of raw_text is the cache key. If the same hash already exists in ResumeRepository,
the cached ResumeProfile is returned without calling enhance_fn.
"""
import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Callable

from app.repositories.database import utcnow_iso
from app.repositories.resume_repository import ResumeRepository
from app.schemas.resume_profile import (
    CertificationEntry,
    EducationEntry,
    ExperienceEntry,
    ResumeProfile,
)

logger = logging.getLogger(__name__)

_SECTION_PATTERNS: dict[str, re.Pattern] = {
    "summary": re.compile(
        r"(?:^|\n)[ \t]*(?:SUMMARY|PROFESSIONAL SUMMARY|ABOUT|OBJECTIVE|PROFILE)[ \t]*(?:\n|:)",
        re.IGNORECASE,
    ),
    "experience": re.compile(
        r"(?:^|\n)[ \t]*(?:EXPERIENCE|WORK EXPERIENCE|EMPLOYMENT|EMPLOYMENT HISTORY"
        r"|PROFESSIONAL EXPERIENCE)[ \t]*(?:\n|:)",
        re.IGNORECASE,
    ),
    "education": re.compile(
        r"(?:^|\n)[ \t]*(?:EDUCATION|ACADEMIC|ACADEMIC BACKGROUND)[ \t]*(?:\n|:)",
        re.IGNORECASE,
    ),
    "skills": re.compile(
        r"(?:^|\n)[ \t]*(?:SKILLS|TECHNICAL SKILLS|TECHNOLOGIES|CORE COMPETENCIES"
        r"|COMPETENCIES)[ \t]*(?:\n|:)",
        re.IGNORECASE,
    ),
    "certifications": re.compile(
        r"(?:^|\n)[ \t]*(?:CERTIFICATIONS?|CERTIFICATES?|CREDENTIALS?|LICENSES?)[ \t]*(?:\n|:)",
        re.IGNORECASE,
    ),
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


class ResumeParseError(Exception):
    """Raised when ResumeParser cannot produce a valid ResumeProfile."""


class ResumeParser:
    """
    Two-phase resume parser. enhance_fn=None (test mode) uses heuristic fields only.
    In production the orchestrator injects enhance_fn bound to the Claude provider.
    """

    def __init__(
        self,
        resume_repository: ResumeRepository,
        enhance_fn: Callable[..., dict] | None = None,
    ) -> None:
        self._repo = resume_repository
        # enhance_fn signature is (raw_text, heuristic_fields, workflow_id=None) -> dict.
        # Older 2-arg callables still work because workflow_id has a default in the
        # production factory and MagicMocks accept any args.
        self._enhance_fn = enhance_fn

    def parse_pdf(
        self, file_path: str, file_name: str, workflow_id: str | None = None
    ) -> ResumeProfile:
        """Extract text from PDF, check cache, parse, and return ResumeProfile.

        ``workflow_id`` is forwarded to ``enhance_fn`` so the resulting LLM call
        is recorded against the run's llm_calls audit. Pass None for ad-hoc
        parses outside a workflow (e.g. resume upload endpoint) — cost is still
        captured by the provider's thread-local last_usage but won't be linked
        to a run.
        """
        try:
            from pdfminer.high_level import extract_text  # noqa: PLC0415
            raw_text: str = extract_text(file_path) or ""
        except Exception as exc:
            raise ResumeParseError(f"Failed to read PDF '{file_path}': {exc}") from exc
        return self.parse_text(raw_text, file_name, workflow_id=workflow_id)

    def parse_text(
        self, raw_text: str, file_name: str, workflow_id: str | None = None
    ) -> ResumeProfile:
        """Parse pre-extracted text. Safe to call in tests without a real PDF."""
        if not raw_text or len(raw_text.strip()) < 50:
            raise ResumeParseError(
                "empty PDF — extracted text is too short to parse (< 50 chars)"
            )

        text_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        cached = self._repo.get_by_raw_text_hash(text_hash)
        if cached:
            logger.info("ResumeParser: cache hit for hash %.8s", text_hash)
            return ResumeProfile.model_validate(json.loads(cached["parsed_profile_json"]))

        fields = self._heuristic_parse(raw_text)

        if self._enhance_fn is not None:
            try:
                enhanced = self._enhance_fn(raw_text, fields, workflow_id)
                fields.update(enhanced)
            except Exception as exc:
                raise ResumeParseError(f"Claude enhancement failed: {exc}") from exc

        resume_id = str(uuid.uuid4())

        def _to_exp(e: dict) -> ExperienceEntry:
            return ExperienceEntry.model_validate(e) if isinstance(e, dict) else e

        def _to_edu(e: dict) -> EducationEntry:
            return EducationEntry.model_validate(e) if isinstance(e, dict) else e

        def _to_cert(c: dict) -> CertificationEntry:
            return CertificationEntry.model_validate(c) if isinstance(c, dict) else c

        profile = ResumeProfile(
            resume_id=resume_id,
            file_name=file_name,
            raw_text=raw_text,
            name=fields.get("name"),
            headline=fields.get("headline"),
            email=fields.get("email"),
            location=fields.get("location"),
            summary=fields.get("summary"),
            experience=[_to_exp(e) for e in fields.get("experience", [])],
            skills=fields.get("skills", []),
            education=[_to_edu(e) for e in fields.get("education", [])],
            certifications=[_to_cert(c) for c in fields.get("certifications", [])],
            parsed_at=utcnow_iso(),
        )

        self._repo.create(
            resume_id=resume_id,
            file_name=file_name,
            raw_text=raw_text,
            raw_text_hash=text_hash,
            parsed_profile=profile.model_dump(),
        )
        return profile

    # ── Heuristic extraction ──────────────────────────────────────────────────

    def _heuristic_parse(self, text: str) -> dict:
        """Best-effort section extraction. Returns whatever can be found; never raises."""
        non_empty_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        name = non_empty_lines[0] if non_empty_lines else None

        email_match = _EMAIL_RE.search(text)
        email = email_match.group(0) if email_match else None

        sections = self._split_sections(text)

        skills: list[str] = []
        skill_text = sections.get("skills", "").strip()
        if skill_text:
            for sep in [",", "|", "•", "·", "\n"]:
                if sep in skill_text:
                    skills = [s.strip() for s in skill_text.split(sep) if s.strip()]
                    break
            if not skills and skill_text:
                skills = [skill_text]

        return {
            "name": name,
            "email": email,
            "summary": sections.get("summary", "").strip() or None,
            "experience": [],   # structural — deferred to Claude enhancement
            "skills": skills,
            "education": [],    # structural — deferred to Claude enhancement
            "certifications": [],
        }

    def _split_sections(self, text: str) -> dict[str, str]:
        """Find section header positions and extract text between them."""
        boundaries: list[tuple[int, str]] = []
        for section_name, pattern in _SECTION_PATTERNS.items():
            for m in pattern.finditer(text):
                boundaries.append((m.end(), section_name))
        boundaries.sort()

        sections: dict[str, str] = {}
        for i, (start, name) in enumerate(boundaries):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            sections[name] = text[start:end].strip()
        return sections
