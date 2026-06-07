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

import re


# Direct identifiers redacted before a resume profile enters any agent LLM
# context (ADR-069). The name is replaced with a placeholder rather than dropped
# so prompt phrasing that references it still reads naturally; email, location,
# and file_name are dropped (set to None). This is quality-neutral: no agent
# conditions on the candidate's name or contact details, and the deterministic
# renderer (resume_text_renderer.compose_resume) re-inserts real identity from
# the STORED profile, not from LLM output.
PII_NAME_PLACEHOLDER = "[CANDIDATE]"
PII_PHONE_PLACEHOLDER = "[PHONE]"
PII_EMAIL_PLACEHOLDER = "[EMAIL]"
_PII_DROP_FIELDS = ("email", "location", "file_name")

# Free-text fields that SURVIVE redaction (agents reason over them) but can still
# carry an inline contact detail - most commonly a phone number on a resume's
# headline/contact line, e.g. "Jane Doe | Security Architect | (555) 123-4567"
# (ADR-069 addendum, 2026-05-30). These are scrubbed deterministically below.
_FREE_TEXT_PII_FIELDS = ("headline", "summary")

# Phone numbers in the common resume layouts: (555) 123-4567, 555-123-4567,
# 555.123.4567, 555 123 4567, +1 555-123-4567, 1-800-555-0199, and bare 10-11
# digit runs. Tuned to NOT match the numeric content that is *not* a phone on a
# resume: years (2019-2021), percentages (300%), counts (50 / 1,000), money
# ($2.5M), GPAs (3.9/4.0), versions (3.11), IPs (10.0.0.1). Each either lacks the
# phone-shaped grouping or is excluded by the digit/dot boundaries. Unusual
# international groupings (e.g. "+44 20 7946 0958") are covered only when they
# fit the area-3-3/4 shape - the rest is documented residual (ADR-069).
_PHONE_RE = re.compile(
    r"(?<![\w.])"                       # left boundary; not after a word char or dot
    r"(?:\+?\d{1,3}[\s.\-]?)?"          # optional country code
    r"(?:"
    r"\(\d{2,4}\)[\s.\-]?\d{3}[\s.\-]?\d{3,4}"   # (xxx) xxx-xxxx
    r"|\d{2,4}[\s.\-]\d{3}[\s.\-]\d{3,4}"        # xxx-xxx-xxxx (separator required)
    r"|\d{10,11}"                                 # bare 10-11 digit run
    r")"
    r"(?![\w.])"                        # right boundary; not before a word char or dot
)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _scrub_contact_pii(text: str) -> str:
    """Replace inline phone numbers and email addresses in free text with
    placeholders (ADR-069 addendum). Deterministic, no LLM. Email is also a
    structured field we drop, but an address written into prose would otherwise
    survive. Email is scrubbed first so a phone-shaped digit run inside an
    address cannot be partially matched."""
    text = _EMAIL_RE.sub(PII_EMAIL_PLACEHOLDER, text)
    text = _PHONE_RE.sub(PII_PHONE_PLACEHOLDER, text)
    return text


def redact_pii_for_llm(profile: dict | None) -> dict:
    """Drop raw_text and redact direct identifiers from a resume profile (ADR-069).

    - ``raw_text`` -> dropped. Only the resume parser (which must read it) and
      the clinic Fidelity Reviewer (ADR-015 evidence-binding, passed top-level
      outside the profile) see raw resume text.
    - ``name`` -> replaced with ``PII_NAME_PLACEHOLDER`` when present (``None``
      stays ``None``).
    - ``email`` / ``location`` / ``file_name`` -> dropped (set to ``None``).
    - ``headline`` / ``summary`` -> inline phone numbers and email addresses
      scrubbed to placeholders (ADR-069 addendum); the prose is otherwise kept
      because agents reason over it.

    Kept (quasi-identifiers the agents reason over): headline, summary, skills,
    skill_groups, experience[], education[], certifications[]. Phone numbers have
    no structured field of their own - they live only in raw_text (dropped here)
    or inline in the free-text fields (scrubbed here).
    """
    if not profile:
        return {}
    out = {k: v for k, v in profile.items() if k != "raw_text"}
    if out.get("name"):
        out["name"] = PII_NAME_PLACEHOLDER
    for field in _PII_DROP_FIELDS:
        if field in out:
            out[field] = None
    for field in _FREE_TEXT_PII_FIELDS:
        val = out.get(field)
        if isinstance(val, str) and val:
            out[field] = _scrub_contact_pii(val)
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


def project_resume_for_scoring(profile: dict | None) -> dict:
    """Scoring-specific resume projection (ADR-086).

    Builds on :func:`trim_resume_profile` (so the ADR-069 PII seam is preserved -
    raw_text dropped, identifiers redacted) and then drops fields the Scoring
    Agent's prompt provably does not read, to shrink the resume payload that is
    re-sent on every per-job scoring call:

      - ``name`` (already a placeholder): scoring never conditions on identity.
      - ``resume_id`` / ``file_name``: metadata, not part of a fit judgment.
      - ``skills``: redundant when ``skill_groups`` is populated - it is the
        de-duped union of the groups (ADR-067), so sending both ships the skill
        list twice. Kept when there are no groups (then it is the only source).
      - ``education[].gpa`` / ``.honors`` (ADR-067): not part of a fit judgment.

    Quality-neutral: the scoring prompt reasons over headline, summary,
    experience[] (title/company/years/description/technologies), skill_groups
    (or skills), education degree, and certifications - all retained. This is the
    same "trace the consumer, drop only unread fields" rule as the other trimmers.
    """
    trimmed = trim_resume_profile(profile)
    if not trimmed:
        return {}
    out = dict(trimmed)
    for meta in ("name", "resume_id", "file_name"):
        out.pop(meta, None)
    if out.get("skill_groups"):
        out.pop("skills", None)
    edu = out.get("education")
    if isinstance(edu, list):
        out["education"] = [
            {k: v for k, v in e.items() if k not in ("gpa", "honors")}
            if isinstance(e, dict) else e
            for e in edu
        ]
    return out


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
