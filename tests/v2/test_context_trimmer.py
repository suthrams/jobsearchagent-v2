"""Tests for app/services/context_trimmer.py — quality-neutral cost cuts.

These tests pin the per-field decisions so a future refactor that adds a new
field to one of the trimmed dicts doesn't accidentally include it (which
would re-bloat the input tokens we just trimmed). Each `keep` set is
intentionally explicit; new fields require a deliberate decision.
"""
from __future__ import annotations

import pytest

from app.services.context_trimmer import (
    PII_EMAIL_PLACEHOLDER,
    PII_NAME_PLACEHOLDER,
    PII_PHONE_PLACEHOLDER,
    project_resume_for_scoring,
    redact_pii_for_llm,
    trim_career_advice,
    trim_resume_profile,
    trim_review,
    trim_score,
)


# -- project_resume_for_scoring (ADR-086) -------------------------------------

def test_scoring_projection_preserves_pii_seam_and_reasoning_fields():
    out = project_resume_for_scoring({
        "resume_id": "r1", "file_name": "cv.pdf", "raw_text": "FULL BLOB",
        "name": "Jane Doe", "email": "jane@example.com",
        "summary": "Senior security architect.",
        "experience": [{"company": "Acme", "title": "Principal Engineer",
                        "start_year": 2019, "description": "Built SIEM",
                        "technologies": ["Python"]}],
        "skill_groups": [{"category": "Security", "skills": ["SIEM", "IR"]}],
        "skills": ["SIEM", "IR"],
        "education": [{"institution": "GT", "degree": "BS", "year": 2010,
                       "gpa": "3.9", "honors": "cum laude"}],
        "certifications": [{"name": "CISSP"}],
    })
    # PII seam (ADR-069): raw_text gone, email dropped
    assert "raw_text" not in out
    assert out.get("email") is None
    # metadata + identity dropped (scoring never reads them)
    for dropped in ("name", "resume_id", "file_name"):
        assert dropped not in out
    # skills is redundant when skill_groups is present -> dropped
    assert "skills" not in out
    assert out["skill_groups"][0]["skills"] == ["SIEM", "IR"]
    # education degree kept; gpa/honors dropped
    assert out["education"][0]["degree"] == "BS"
    assert "gpa" not in out["education"][0] and "honors" not in out["education"][0]
    # reasoning fields scoring uses are retained
    assert out["summary"] and out["experience"][0]["description"] == "Built SIEM"
    assert out["certifications"][0]["name"] == "CISSP"


def test_scoring_projection_keeps_skills_when_no_groups():
    out = project_resume_for_scoring({
        "raw_text": "x", "skills": ["Python", "Go"], "skill_groups": [],
    })
    assert out["skills"] == ["Python", "Go"]  # only source -> kept


def test_scoring_projection_handles_empty_or_none():
    assert project_resume_for_scoring(None) == {}
    assert project_resume_for_scoring({}) == {}


# ── trim_resume_profile / redact_pii_for_llm (ADR-069) ───────────────────────

def test_resume_profile_drops_raw_text_and_redacts_identifiers():
    out = trim_resume_profile({
        "name": "Jane Smith",
        "headline": "Engineer",
        "email": "jane@example.com",
        "location": "Atlanta, GA",
        "summary": "...",
        "skills": ["Python"],
        "raw_text": "JANE SMITH\n... (huge blob)",
    })
    # raw_text gone; direct identifiers redacted (ADR-069)
    assert "raw_text" not in out
    assert out["name"] == PII_NAME_PLACEHOLDER
    assert out["email"] is None
    assert out["location"] is None
    # quasi-identifiers the agents reason over are kept
    assert out["headline"] == "Engineer"
    assert out["skills"] == ["Python"]


def test_resume_profile_keeps_reasoning_fields():
    """Experience / education / skills / headline / summary are load-bearing and
    must survive redaction; only direct identifiers + raw_text are removed."""
    profile = {
        "name": "X", "headline": "Y", "email": "a@b.c", "location": "Atlanta",
        "summary": "S", "experience": [{"company": "A"}], "skills": ["Py"],
        "education": [{"institution": "MIT"}], "certifications": [],
        "skill_groups": [{"category": "Cloud", "skills": ["AWS"]}],
    }
    out = trim_resume_profile(profile)
    assert out["headline"] == "Y"
    assert out["summary"] == "S"
    assert out["experience"] == [{"company": "A"}]
    assert out["skills"] == ["Py"]
    assert out["education"] == [{"institution": "MIT"}]
    assert out["skill_groups"] == [{"category": "Cloud", "skills": ["AWS"]}]
    # identifiers redacted
    assert out["name"] == PII_NAME_PLACEHOLDER
    assert out["email"] is None
    assert out["location"] is None


def test_redact_drops_file_name():
    out = redact_pii_for_llm({"file_name": "John_Doe_CV.pdf", "skills": ["Py"]})
    assert out["file_name"] is None
    assert out["skills"] == ["Py"]


def test_redact_none_name_stays_none():
    """A missing/empty name is left as-is (no spurious placeholder)."""
    assert redact_pii_for_llm({"name": None, "skills": []})["name"] is None
    assert redact_pii_for_llm({"name": "", "skills": []})["name"] == ""


def test_trim_resume_profile_delegates_to_redact():
    profile = {"name": "Jane", "email": "j@x.c", "raw_text": "blob", "skills": ["Py"]}
    assert trim_resume_profile(profile) == redact_pii_for_llm(profile)


def test_resume_profile_handles_empty_or_none():
    assert trim_resume_profile(None) == {}
    assert trim_resume_profile({}) == {}
    assert redact_pii_for_llm(None) == {}
    assert redact_pii_for_llm({}) == {}


# ── free-text contact scrubbing (ADR-069 addendum) ───────────────────────────

@pytest.mark.parametrize("phone", [
    "(555) 123-4567",
    "555-123-4567",
    "555.123.4567",
    "555 123 4567",
    "+1 555-123-4567",
    "1-800-555-0199",
    "5551234567",
])
def test_headline_phone_is_scrubbed(phone):
    """A phone number on the headline/contact line is the common case (ADR-069
    addendum). It must not reach the model."""
    out = redact_pii_for_llm({
        "headline": f"Jane Doe | Security Architect | {phone}",
        "skills": ["Python"],
    })
    assert phone not in out["headline"]
    assert PII_PHONE_PLACEHOLDER in out["headline"]
    # surrounding prose is preserved
    assert "Security Architect" in out["headline"]


def test_summary_phone_and_email_scrubbed():
    out = redact_pii_for_llm({
        "summary": "Reach me at jane@example.com or (555) 123-4567 anytime.",
    })
    assert "jane@example.com" not in out["summary"]
    assert "(555) 123-4567" not in out["summary"]
    assert PII_EMAIL_PLACEHOLDER in out["summary"]
    assert PII_PHONE_PLACEHOLDER in out["summary"]


@pytest.mark.parametrize("text", [
    "Reduced costs 300% over 2019-2021.",
    "Led 50 engineers across 12 teams.",
    "Grew revenue to $2,500,000 in FY2024.",
    "Maintained 99.99% uptime on Python 3.11.",
    "GPA 3.9/4.0; deployed to 10.0.0.1.",
    "Scaled from 1,000 to 1,000,000 users.",
])
def test_resume_metrics_are_not_mistaken_for_phone(text):
    """False-positive guard: numeric resume content (years, percentages, counts,
    money, GPA, versions, IPs) must survive untouched - it is load-bearing for
    fit reasoning."""
    out = redact_pii_for_llm({"summary": text})
    assert out["summary"] == text
    assert PII_PHONE_PLACEHOLDER not in out["summary"]


def test_scrub_leaves_clean_free_text_unchanged():
    profile = {"headline": "Senior Security Architect", "summary": "Cloud and IAM leader."}
    out = redact_pii_for_llm(profile)
    assert out["headline"] == "Senior Security Architect"
    assert out["summary"] == "Cloud and IAM leader."


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
    """Pin the kept fields against the actual CareerAdvice schema. If a refactor
    drops or renames a field this trim depends on, the test fails."""
    advice = {
        "job_id": "j",
        "positioning_summary": "Lead with platform leadership.",
        "recommended_positioning": "Pitch yourself as a platform-engineering leader.",
        "skills_to_strengthen": ["public speaking", "people management"],
        "recommended_next_action": "Apply",
        # Fields downstream agents don't need
        "resume_gaps": ["Cloud experience not surfaced"],
        "career_gaps": ["No formal compliance experience"],
        "role_fit_assessment": "Strong fit on technical track.",
        "experience_to_collect": ["Lead a customer-facing project"],
        "thirty_sixty_ninety_day_plan": ["..."],
        "confidence": 75,
    }
    out = trim_career_advice(advice)
    assert set(out.keys()) == {
        "positioning_summary", "recommended_positioning",
        "skills_to_strengthen", "recommended_next_action",
    }


def test_advice_kept_fields_exist_in_career_advice_schema():
    """Regression: trim_career_advice once kept 'key_strengths_to_lead_with',
    a field that doesn't exist on CareerAdvice. Trim was a no-op for that
    key. This test catches the same class of drift."""
    from app.schemas.career_advice import CareerAdvice
    schema_fields = set(CareerAdvice.model_fields.keys())
    # Reverse-engineer the keep set by passing every schema field as input.
    full_advice = {f: "x" for f in schema_fields}
    out = trim_career_advice(full_advice)
    drift = set(out.keys()) - schema_fields
    assert not drift, (
        f"trim_career_advice keeps fields that don't exist in CareerAdvice: {sorted(drift)}"
    )


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
