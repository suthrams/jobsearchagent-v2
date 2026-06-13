"""BUG-014: agent prompt output instructions must track the agent's schema.

Two prompts had drifted to instruct fields absent from their Pydantic schema
(fidelity_reviewer: passed_suggestions/overall_verdict; review_auditor:
audit_passed). These guards pin them to the real schema discriminators and forbid
the stale names from returning.
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS = Path(__file__).resolve().parents[2] / "app" / "prompts" / "agents"


def _prompt(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def test_fidelity_reviewer_prompt_matches_schema():
    p = _prompt("fidelity_reviewer.txt")
    # Stale field names must not return.
    assert "passed_suggestions" not in p
    assert "failed_suggestions" not in p
    assert "overall_verdict" not in p
    # Real FidelityReview discriminators must be present.
    assert "overall_fidelity_status" in p
    assert "approval_recommendation" in p


def test_review_auditor_prompt_matches_schema():
    p = _prompt("review_auditor.txt")
    # Stale field name must not return.
    assert "audit_passed" not in p
    # Real ReviewAudit fields must be present.
    assert "stop_recommendation" in p
    assert "missing_analysis_points" in p
    # The contradiction is resolved: missed gaps are an annotation, not a rewrite.
    low = p.lower()
    assert "annotat" in low
