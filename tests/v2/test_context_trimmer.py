"""Tests for app/services/context_trimmer.py — quality-neutral cost cuts.

These tests pin the per-field decisions so a future refactor that adds a new
field to one of the trimmed dicts doesn't accidentally include it (which
would re-bloat the input tokens we just trimmed). Each `keep` set is
intentionally explicit; new fields require a deliberate decision.
"""
from __future__ import annotations

import pytest

from app.services.context_trimmer import (
    trim_career_advice,
    trim_resume_profile,
    trim_review,
    trim_score,
)


# ── trim_resume_profile ──────────────────────────────────────────────────────

def test_resume_profile_drops_raw_text():
    out = trim_resume_profile({
        "name": "Jane",
        "headline": "Engineer",
        "summary": "...",
        "skills": ["Python"],
        "raw_text": "JANE SMITH\n... (huge blob)",
    })
    assert "raw_text" not in out
    assert out["name"] == "Jane"
    assert out["headline"] == "Engineer"


def test_resume_profile_keeps_all_other_fields():
    profile = {
        "name": "X", "headline": "Y", "email": "a@b.c", "location": "Atlanta",
        "summary": "S", "experience": [{"company": "A"}], "skills": ["Py"],
        "education": [{"institution": "MIT"}], "certifications": [],
    }
    out = trim_resume_profile(profile)
    for k in profile:
        assert k in out
    assert out == profile


def test_resume_profile_handles_empty_or_none():
    assert trim_resume_profile(None) == {}
    assert trim_resume_profile({}) == {}


# ── trim_review ──────────────────────────────────────────────────────────────

def test_review_keeps_only_actionable_fields():
    review = {
        "job_id": "j", "resume_id": "r",
        "overall_fit_summary": "Strong match.",
        "critical_gaps": ["No Kafka"],
        "resume_only_gaps": ["Cloud not surfaced"],
        "career_gaps_observed": ["No SOC 2"],
        "section_reviews": [{"section_name": "Experience", "current_issue": "..."}],
        "suggested_improvements": ["Reframe AWS bullets"],
        "questions_for_user": ["Which projects mattered?"],
        "confidence": 80,
    }
    out = trim_review(review)
    assert set(out.keys()) == {
        "overall_fit_summary", "critical_gaps", "resume_only_gaps", "career_gaps_observed",
    }
    # Specifically verify each dropped field is gone — these add real input tokens
    assert "section_reviews" not in out
    assert "suggested_improvements" not in out
    assert "questions_for_user" not in out
    assert "confidence" not in out


def test_review_handles_partial_input():
    out = trim_review({"overall_fit_summary": "x", "critical_gaps": []})
    assert out == {"overall_fit_summary": "x", "critical_gaps": []}


def test_review_handles_empty():
    assert trim_review(None) == {}
    assert trim_review({}) == {}


# ── trim_career_advice ───────────────────────────────────────────────────────

def test_advice_keeps_only_actionable_fields():
    advice = {
        "job_id": "j",
        "positioning_summary": "Lead with platform leadership.",
        "recommended_next_action": "Apply",
        "key_strengths_to_lead_with": ["distributed systems"],
        # Verbose fields downstream agents don't need
        "track_breakdown": {"ic": "...", "architect": "...", "management": "..."},
        "growth_recommendations": ["...", "...", "..."],
        "longer_term_outlook": "Some prose...",
    }
    out = trim_career_advice(advice)
    assert set(out.keys()) == {
        "positioning_summary", "recommended_next_action", "key_strengths_to_lead_with",
    }


def test_advice_handles_empty():
    assert trim_career_advice(None) == {}
    assert trim_career_advice({}) == {}


# ── trim_score ───────────────────────────────────────────────────────────────

def test_score_keeps_per_track_scores_and_summary():
    score = {
        "overall_score": 82,
        "technical_score": 88,
        "architecture_score": 75,
        "leadership_score": 60,
        "domain_score": 70,
        "match_summary": "Strong technical fit.",
        # Dropped: derived elsewhere
        "strengths": ["Python", "K8s"],
        "gaps": ["No Kafka"],
        "recommended_next_action": "Apply",
    }
    out = trim_score(score)
    assert set(out.keys()) == {
        "overall_score", "technical_score", "architecture_score",
        "leadership_score", "domain_score", "match_summary",
    }


# ── PromptLoader cached-context plumbing ─────────────────────────────────────

def test_prompt_loader_emits_separate_cached_block_when_cached_context_set(tmp_path):
    """When context contains a "_cached" key, PromptLoader puts it in a
    second cached SystemMessage. Both system blocks have cache_control:ephemeral."""
    from app.providers.prompt_loader import PromptLoader

    # Minimal prompts dir for this test
    prompts = tmp_path / "prompts"
    (prompts / "shared").mkdir(parents=True)
    (prompts / "agents").mkdir(parents=True)
    (prompts / "shared" / "guardrails.txt").write_text("Guardrails.", encoding="utf-8")
    (prompts / "agents" / "test_agent.txt").write_text(
        "# version: 1\nRole: do the thing.", encoding="utf-8"
    )

    loader = PromptLoader(prompts_dir=prompts)
    messages = loader.assemble("test_agent", {
        "_cached": {"resume_profile": {"name": "Jane", "skills": ["Python"]}},
        "job_id": "j1",
        "job_description": "Build things.",
    })

    # Three messages: agent prompt (cached) + cached static ctx + per-call ctx
    assert len(messages) == 3
    # First: agent prompt with cache_control
    block1 = messages[0].content[0]
    assert block1["cache_control"] == {"type": "ephemeral"}
    assert "Guardrails." in block1["text"]
    # Second: cached static context with cache_control
    block2 = messages[1].content[0]
    assert block2["cache_control"] == {"type": "ephemeral"}
    assert "STATIC CONTEXT" in block2["text"]
    assert "resume_profile" in block2["text"]
    assert "Jane" in block2["text"]
    # Third: per-call human context — _cached must NOT appear (it was extracted)
    human = messages[2].content
    assert "_cached" not in human
    assert "job_description" in human
    assert "Build things." in human


def test_prompt_loader_backwards_compat_no_cached_context(tmp_path):
    """When _cached is absent, behavior is unchanged: 2 messages (system + human)."""
    from app.providers.prompt_loader import PromptLoader

    prompts = tmp_path / "prompts"
    (prompts / "shared").mkdir(parents=True)
    (prompts / "agents").mkdir(parents=True)
    (prompts / "shared" / "guardrails.txt").write_text("Guardrails.", encoding="utf-8")
    (prompts / "agents" / "test_agent.txt").write_text(
        "# version: 1\nRole: do.", encoding="utf-8"
    )

    loader = PromptLoader(prompts_dir=prompts)
    messages = loader.assemble("test_agent", {
        "job_id": "j1",
        "resume_profile": {"name": "Jane"},  # NOT under _cached
    })

    assert len(messages) == 2
    # resume_profile should appear in the human message, same as before
    assert "resume_profile" in messages[1].content
    assert "Jane" in messages[1].content


def test_prompt_loader_empty_cached_context_is_skipped(tmp_path):
    """An empty _cached dict shouldn't add a second block (no point caching nothing)."""
    from app.providers.prompt_loader import PromptLoader

    prompts = tmp_path / "prompts"
    (prompts / "shared").mkdir(parents=True)
    (prompts / "agents").mkdir(parents=True)
    (prompts / "shared" / "guardrails.txt").write_text("Guardrails.", encoding="utf-8")
    (prompts / "agents" / "test_agent.txt").write_text(
        "# version: 1\nRole: do.", encoding="utf-8"
    )

    loader = PromptLoader(prompts_dir=prompts)
    messages = loader.assemble("test_agent", {
        "_cached": {},  # empty
        "job_id": "j1",
    })

    assert len(messages) == 2  # no second cached block
