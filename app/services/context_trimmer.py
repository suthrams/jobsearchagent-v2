"""Context trimmers for high-cost agents (advisor, coach, tailoring).

Each function takes a full agent-output dict and returns a slimmed-down
version that keeps only fields downstream agents actually consume. Cuts
input-token cost across the agents that get this context, with no quality
impact (nothing reads the dropped fields).

See CHANGELOG 2026-05-05 "Context payload trim" for the analysis. Each
field deletion is justified by tracing the downstream agents to confirm
no reads of that field exist.

Pure functions: no I/O, no logging, no side effects. Safe for use anywhere
context is being assembled.
"""
from __future__ import annotations


# Direct identifiers redacted before a resume profile enters any agent LLM
# context (ADR-069). The name is replaced with a placeholder rather than dropped
# so prompt phrasing that references it still reads naturally; email, location,
# and file_name are dropped (set to None). This is quality-neutral: no agent
# conditions on the candidate's name or contact details, and the deterministic
# renderer (resume_text_renderer.compose_resume) re-inserts real identity from
# the STORED profile, not from LLM output.
PII_NAME_PLACEHOLDER = "[CANDIDATE]"
_PII_DROP_FIELDS = ("email", "location", "file_name")


def redact_pii_for_llm(profile: dict | None) -> dict:
    """Drop raw_text and redact direct identifiers from a resume profile (ADR-069).

    - ``raw_text`` -> dropped. Only the resume parser (which must read it) and
      the clinic Fidelity Reviewer (ADR-015 evidence-binding, passed top-level
      outside the profile) see raw resume text.
    - ``name`` -> replaced with ``PII_NAME_PLACEHOLDER`` when present (``None``
      stays ``None``).
    - ``email`` / ``location`` / ``file_name`` -> dropped (set to ``None``).

    Kept (quasi-identifiers the agents reason over): headline, summary, skills,
    skill_groups, experience[], education[], certifications[]. Phone and street
    address are not structured profile fields - they live only in raw_text,
    which is dropped here.
    """
    if not profile:
        return {}
    out = {k: v for k, v in profile.items() if k != "raw_text"}
    if out.get("name"):
        out["name"] = PII_NAME_PLACEHOLDER
    for field in _PII_DROP_FIELDS:
        if field in out:
            out[field] = None
    return out


def trim_resume_profile(profile: dict | None) -> dict:
    """Prepare a resume profile for an agent LLM context.

    Thin wrapper over :func:`redact_pii_for_llm` (ADR-069). Kept as the
    established name every context-build site imports; folding redaction in here
    means every existing caller (scoring, critic, auditor, advisor, coach,
    tailoring, tailoring-fidelity) drops raw_text AND redacts direct identifiers
    for free.

    Justification for the kept fields: every downstream agent reads structured
    fields (headline, summary, experience[], skills, education) - none reads the
    candidate's name, email, or location. Dropping raw_text also saves ~1-2K
    input tokens per call (it is the largest field in the profile).
    """
    return redact_pii_for_llm(profile)


def trim_review(review: dict | None) -> dict:
    """Keep only the fields downstream consumers (advisor / coach / tailoring) read.

    Drops:
      - section_reviews: nested per-section critique, only useful for the in-graph
        review loop itself; downstream agents work off the consolidated gaps.
      - suggested_improvements: advisor and coach derive their own suggestions;
        passing the critic's would bias their output.
      - questions_for_user: clarifying questions for the user, not for downstream
        agents to act on.
      - confidence: useful for UI display; agents don't condition on it.

    Keeps the load-bearing fields downstream actually reads:
      - overall_fit_summary
      - critical_gaps
      - resume_only_gaps
      - career_gaps_observed (per ADR-013, this distinction is critical)

    Typical saving: 0.5-1.5K tokens depending on review depth.
    """
    if not review:
        return {}
    keep = {"overall_fit_summary", "critical_gaps", "resume_only_gaps", "career_gaps_observed"}
    return {k: v for k, v in review.items() if k in keep}


def trim_career_advice(advice: dict | None) -> dict:
    """Keep the actionable fields the coach + tailoring agent read.

    CareerAdvice schema fields kept (all from app/schemas/career_advice.py):
      - positioning_summary       — the one-line pitch
      - recommended_positioning   — the richer positioning prose
      - skills_to_strengthen      — signals what to emphasize in tailoring
      - recommended_next_action   — apply / interview-prep / hold

    Dropped (not consumed by downstream agents):
      - resume_gaps, career_gaps  — already in resume_review (the trimmed
        review still carries critical_gaps / resume_only_gaps / career_gaps_observed)
      - role_fit_assessment       — overlaps with positioning_summary
      - experience_to_collect     — for the user, not for downstream agents
      - thirty_sixty_ninety_day_plan — same
      - confidence                — UI display only

    Typical saving: 0.5-1K tokens.
    """
    if not advice:
        return {}
    keep = {
        "positioning_summary",
        "recommended_positioning",
        "skills_to_strengthen",
        "recommended_next_action",
    }
    return {k: v for k, v in advice.items() if k in keep}


def trim_score(score: dict | None) -> dict:
    """Keep just the per-track scores + the headline summary.

    Drops:
      - strengths / gaps lists (advisor / coach derive these from the review)
      - recommended_next_action (lives in career_advice already)

    Typical saving: ~0.3K tokens.
    """
    if not score:
        return {}
    keep = {
        "overall_score", "technical_score", "architecture_score",
        "leadership_score", "domain_score", "match_summary",
    }
    return {k: v for k, v in score.items() if k in keep}
