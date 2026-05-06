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


def trim_resume_profile(profile: dict | None) -> dict:
    """Drop raw_text from the resume profile.

    Justification: every downstream agent (scoring, critic, advisor, coach,
    tailoring) reads structured fields (name, headline, summary, experience[],
    skills, etc.). Only Fidelity Reviewer needs raw_text to validate that
    suggestions cite real resume content (ADR-015), and FidelityReviewer is
    given the raw_text separately if needed.

    Typical saving: 1-2K tokens per agent call (raw resume text is the largest
    field in the profile).
    """
    if not profile:
        return {}
    out = {k: v for k, v in profile.items() if k != "raw_text"}
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

    Drops verbose track-by-track prose; keeps the positioning thesis and
    recommended action that interview_coach and tailoring_agent actually
    condition on.

    Typical saving: 0.5-1K tokens.
    """
    if not advice:
        return {}
    keep = {"positioning_summary", "recommended_next_action", "key_strengths_to_lead_with"}
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
