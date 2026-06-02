"""ADR-072 T1: the deterministic seed adapter tailored_draft_to_overhaul().

Converts a job's TailoredResumeDraft into a clinic ResumeOverhaul so the reused
chat-revise loop + export can start from the job-tailored resume. The adapter is
the load-bearing seam between the two schemas; these tests pin its mapping,
evidence preservation, claim-type filtering, and determinism.
"""
from __future__ import annotations

from app.schemas.resume_clinic import ResumeOverhaul
from app.schemas.tailored_resume_draft import TailoredBullet, TailoredResumeDraft
from app.services.tailoring_chat_seed import tailored_draft_to_overhaul


def _bullet(claim_type, *, original="orig", suggested="better text",
            evidence="resume line X", section="experience:Acme:Staff Engineer",
            risk="low"):
    return TailoredBullet(
        original_text=original,
        suggested_text=suggested,
        supporting_evidence=evidence,
        claim_type=claim_type,
        fidelity_risk=risk,
        section_label=section,
        impact_rationale="matches JD requirement",
    )


def _draft(**kw):
    base = dict(job_id="j1", resume_id="r1")
    base.update(kw)
    return TailoredResumeDraft(**base)


# ── claim_type mapping ────────────────────────────────────────────────────────

def test_reword_maps_to_restate():
    draft = _draft(experience_bullet_suggestions=[_bullet("reword")])
    overhaul = tailored_draft_to_overhaul(draft)
    assert len(overhaul.rewrites) == 1
    assert overhaul.rewrites[0].claim_type == "restate"


def test_emphasize_maps_to_reframe():
    draft = _draft(experience_bullet_suggestions=[_bullet("emphasize")])
    overhaul = tailored_draft_to_overhaul(draft)
    assert overhaul.rewrites[0].claim_type == "reframe"


# ── gap / remove are dropped (ADR-072 Q2 revised) ─────────────────────────────

def test_gap_is_dropped():
    # gap/remove have empty suggested_text per the tailoring schema semantics
    draft = _draft(experience_bullet_suggestions=[_bullet("gap", suggested="")])
    assert tailored_draft_to_overhaul(draft).rewrites == []


def test_remove_is_dropped():
    draft = _draft(experience_bullet_suggestions=[_bullet("remove", suggested="")])
    assert tailored_draft_to_overhaul(draft).rewrites == []


def test_mixed_keeps_only_reword_and_emphasize():
    draft = _draft(experience_bullet_suggestions=[
        _bullet("reword", original="a"),
        _bullet("gap", original="b", suggested=""),
        _bullet("emphasize", original="c"),
        _bullet("remove", original="d", suggested=""),
    ])
    overhaul = tailored_draft_to_overhaul(draft)
    assert [r.original_text for r in overhaul.rewrites] == ["a", "c"]


# ── evidence preservation + binding ───────────────────────────────────────────

def test_preserves_fields_and_evidence():
    draft = _draft(summary_suggestions=[_bullet(
        "reword", original="O", suggested="S", evidence="E", section="summary")])
    r = tailored_draft_to_overhaul(draft).rewrites[0]
    assert (r.original_text, r.suggested_text, r.supporting_evidence, r.section_label) == (
        "O", "S", "E", "summary")


def test_skips_bullet_with_empty_evidence_from_dict():
    # A human-edited draft dict could carry an empty evidence; skip (binding).
    draft = {"job_id": "j1", "resume_id": "r1", "experience_bullet_suggestions": [
        {"claim_type": "reword", "original_text": "o", "suggested_text": "s",
         "supporting_evidence": "", "section_label": "summary"},
    ]}
    assert tailored_draft_to_overhaul(draft).rewrites == []


def test_skips_bullet_with_empty_suggested():
    draft = {"job_id": "j1", "resume_id": "r1", "experience_bullet_suggestions": [
        {"claim_type": "reword", "original_text": "o", "suggested_text": "   ",
         "supporting_evidence": "e", "section_label": "summary"},
    ]}
    assert tailored_draft_to_overhaul(draft).rewrites == []


# ── ordering, reorg, skills, dict input, determinism ──────────────────────────

def test_walks_headline_summary_experience_in_order():
    draft = _draft(
        headline_suggestions=[_bullet("reword", original="H", section="headline")],
        summary_suggestions=[_bullet("reword", original="S", section="summary")],
        experience_bullet_suggestions=[_bullet("reword", original="E")],
    )
    assert [r.original_text for r in tailored_draft_to_overhaul(draft).rewrites] == ["H", "S", "E"]


def test_reorganization_is_empty():
    draft = _draft(experience_bullet_suggestions=[_bullet("reword")])
    overhaul = tailored_draft_to_overhaul(draft)
    assert overhaul.reorganization.section_order == []
    assert overhaul.reorganization.moves == []


def test_skills_section_suggestions_not_seeded():
    # bare strings, no evidence -> cannot form an evidence-bound rewrite
    draft = _draft(skills_section_suggestions=["Kubernetes", "Terraform"])
    assert tailored_draft_to_overhaul(draft).rewrites == []


def test_accepts_dict_input():
    draft = {"job_id": "j1", "resume_id": "r1", "summary_suggestions": [
        {"claim_type": "emphasize", "original_text": "o", "suggested_text": "s",
         "supporting_evidence": "e", "section_label": "summary"},
    ]}
    overhaul = tailored_draft_to_overhaul(draft)
    assert isinstance(overhaul, ResumeOverhaul)
    assert overhaul.rewrites[0].claim_type == "reframe"


def test_deterministic():
    draft = _draft(experience_bullet_suggestions=[
        _bullet("reword", original="a"), _bullet("emphasize", original="b")])
    assert tailored_draft_to_overhaul(draft).model_dump() == \
        tailored_draft_to_overhaul(draft).model_dump()


def test_empty_draft_yields_empty_overhaul():
    overhaul = tailored_draft_to_overhaul(_draft())
    assert overhaul.rewrites == []
