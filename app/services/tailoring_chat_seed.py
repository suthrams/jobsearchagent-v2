"""Seed a Resume Clinic overhaul from a job's tailored resume draft (ADR-072).

Deterministic, no LLM. Converts a `TailoredResumeDraft`'s bullets into the
clinic's `ResumeOverhaul` shape so the reused chat-revise loop + export
(ADR-066/068) can operate on the job-tailored resume as their starting point.

This is the inverse of `resume_clinic_runner._CLAIM_TYPE_MAP` (clinic -> tailoring):
  reword    -> restate
  emphasize -> reframe

Only `reword`/`emphasize` bullets are seeded (they are real rewrites carrying
`supporting_evidence`). `gap` is never content (it would be fabrication).
`remove` is dropped: the reused renderer cannot apply a per-bullet removal on
export (ADR-072 "inherited limitation"), so seeding it would be a silent no-op.
`skills_section_suggestions` are bare strings with no `supporting_evidence` and
cannot form an evidence-bound `RewriteSuggestion`, so they are not seeded either.
"""
from __future__ import annotations

from app.schemas.resume_clinic import Reorganization, ResumeOverhaul, RewriteSuggestion
from app.schemas.tailored_resume_draft import TailoredResumeDraft

# Tailoring claim_type -> clinic claim_type. Inverse of the forward map in
# resume_clinic_runner. Only the two rewrite-bearing types map; gap/remove drop.
_TAILORED_TO_CLINIC_CLAIM = {
    "reword": "restate",
    "emphasize": "reframe",
}

# Walked in resume order; list order within each section is preserved.
_BULLET_SECTIONS = (
    "headline_suggestions",
    "summary_suggestions",
    "experience_bullet_suggestions",
)


def _as_dict(draft: TailoredResumeDraft | dict) -> dict:
    if hasattr(draft, "model_dump"):
        return draft.model_dump()
    return dict(draft or {})


def tailored_draft_to_overhaul(draft: TailoredResumeDraft | dict) -> ResumeOverhaul:
    """Build a `ResumeOverhaul` seed from a tailored draft (or its persisted dict).

    Deterministic: bullets are walked in resume order (headline, summary,
    experience) preserving list order. Returns an overhaul with an empty
    reorganization (the seed carries only rewrites) and one `RewriteSuggestion`
    per seedable bullet (reword/emphasize with a non-empty suggestion and
    evidence). gap / remove / unknown claim_types are skipped.
    """
    d = _as_dict(draft)
    rewrites: list[RewriteSuggestion] = []
    for section in _BULLET_SECTIONS:
        for bullet in d.get(section) or []:
            bd = bullet if isinstance(bullet, dict) else dict(bullet)
            clinic_claim = _TAILORED_TO_CLINIC_CLAIM.get((bd.get("claim_type") or "").strip())
            if clinic_claim is None:
                continue  # gap / remove / unknown -> not seeded
            suggested = (bd.get("suggested_text") or "").strip()
            evidence = (bd.get("supporting_evidence") or "").strip()
            if not suggested or not evidence:
                # Empty suggestion can't render (renderer skips it); evidence is
                # required to keep the seed evidence-bound. Skip defensively.
                continue
            rewrites.append(RewriteSuggestion(
                section_label=bd.get("section_label") or "",
                original_text=bd.get("original_text") or "",
                suggested_text=suggested,
                claim_type=clinic_claim,
                supporting_evidence=evidence,
            ))
    return ResumeOverhaul(
        reorganization=Reorganization(section_order=[], moves=[]),
        rewrites=rewrites,
    )
