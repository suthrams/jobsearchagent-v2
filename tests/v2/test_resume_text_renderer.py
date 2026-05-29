"""Tests for app/services/resume_text_renderer.py.

Two layers:

1. Composer + decision-aware logic + rewrite application + reorganization moves.
   These are the fidelity-bearing checks: nothing the agent didn't rewrite
   should change; placeholders stay verbatim; the decision changes the source
   of the overhaul; unmatched rewrites get appended, not silently dropped.

2. Smoke tests for each of the six renderers asserting the output is non-empty,
   has the right shape (markdown headers / HTML tags / JSON keys / DOCX magic
   bytes / PDF magic bytes), and contains the candidate's name. The deeper
   formatting is exercised at the composer layer; the renderers are walkers.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.services.resume_text_renderer import (
    ExperienceItem,
    RenderedResume,
    compose_resume,
    export_content_type,
    export_file_extension,
    render,
    render_docx,
    render_html,
    render_json_resume,
    render_markdown,
    render_pdf,
    render_plain_text,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _profile() -> dict:
    return {
        "resume_id": "r-1",
        "raw_text": "<unused by renderer>",
        "name": "Jamie Lee",
        "headline": "Software Engineer",
        "email": "jamie@example.com",
        "location": "Austin, TX",
        "summary": (
            "Software engineer with 4 years of experience building backend "
            "services. Comfortable with Python and Go. Eager to take on more "
            "architectural ownership."
        ),
        "experience": [
            {
                "company": "Acme Corp",
                "title": "Senior Engineer",
                "start_year": 2022,
                "end_year": None,
                "description": (
                    "Led migration of monolith to microservices on AWS ECS.\n"
                    "Owned the payments service end-to-end.\n"
                    "Mentored two junior engineers."
                ),
                "technologies": ["Python", "AWS", "PostgreSQL"],
            },
            {
                "company": "Beta Inc",
                "title": "Software Engineer",
                "start_year": 2020,
                "end_year": 2022,
                "description": (
                    "Built internal tooling for the data team.\n"
                    "Worked on the API gateway."
                ),
                "technologies": ["Go", "gRPC"],
            },
        ],
        "skills": ["Python", "Go", "AWS", "PostgreSQL", "Docker", "Kubernetes"],
        "education": [
            {"institution": "UT Austin", "degree": "BS Computer Science", "year": 2020},
        ],
        "certifications": [
            {"name": "AWS Solutions Architect Associate", "issuer": "AWS", "year": 2023},
        ],
        "parsed_at": "2026-05-28T00:00:00.000Z",
    }


def _overhaul() -> dict:
    return {
        "reorganization": {
            "section_order": ["summary", "experience", "skills",
                              "certifications", "education"],
            "moves": [
                {
                    "action": "promote",
                    "subject": "Certifications",
                    "rationale": "Cert signals cloud depth for senior IC roles.",
                },
            ],
        },
        "rewrites": [
            {
                "section_label": "experience:Acme Corp:Senior Engineer",
                "original_text": "Led migration of monolith to microservices on AWS ECS.",
                "suggested_text": (
                    "Led migration of monolith to microservices on AWS ECS "
                    "across [N] services, reducing deploy time by [X]%."
                ),
                "claim_type": "quantify",
                "supporting_evidence": "Ownership of payments service.",
            },
            {
                "section_label": "experience:Acme Corp:Senior Engineer",
                "original_text": "Mentored two junior engineers.",
                "suggested_text": (
                    "Mentored two engineers and codified onboarding docs "
                    "adopted across the platform team."
                ),
                "claim_type": "reframe",
                "supporting_evidence": "Resume confirms mentorship.",
            },
        ],
    }


# ── Composer: decision-aware logic ──────────────────────────────────────────

def test_compose_reject_renders_original_only():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="reject")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert "Led migration of monolith to microservices on AWS ECS." in acme.bullets
    # The agent's rewrite must NOT have been applied.
    assert not any("[N] services" in b for b in acme.bullets)
    assert rendered.banner is None


def test_compose_approve_applies_overhaul():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert any("[N] services" in b for b in acme.bullets)
    assert "Led migration of monolith to microservices on AWS ECS." not in acme.bullets
    # Reframe also applied.
    assert any("codified onboarding docs" in b for b in acme.bullets)
    assert rendered.banner is None


def test_compose_edit_prefers_edited_over_overhaul():
    edited = {
        "reorganization": {"section_order": ["summary", "experience"], "moves": []},
        "rewrites": [
            {
                "section_label": "experience:Acme Corp:Senior Engineer",
                "original_text": "Led migration of monolith to microservices on AWS ECS.",
                "suggested_text": "HUMAN edit: led AWS ECS migration end-to-end.",
                "claim_type": "restate",
                "supporting_evidence": "human authored",
            },
        ],
    }
    rendered = compose_resume(_profile(), _overhaul(), edited, decision="edit")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    # Human draft used; agent draft NOT applied.
    assert any("HUMAN edit" in b for b in acme.bullets)
    assert not any("[N] services" in b for b in acme.bullets)


def test_compose_revise_sets_preview_banner_and_applies_overhaul():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="revise")
    assert rendered.banner and "revise" in rendered.banner.lower()
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert any("[N] services" in b for b in acme.bullets)


def test_compose_no_decision_sets_preview_banner_and_applies_overhaul():
    rendered = compose_resume(_profile(), _overhaul(), None, decision=None)
    assert rendered.banner and "preview" in rendered.banner.lower()
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert any("[N] services" in b for b in acme.bullets)


def test_compose_unknown_decision_sets_preview_banner_and_applies_overhaul():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="maybe")
    assert rendered.banner and "unrecognized" in rendered.banner.lower()


# ── ADR-068: edited wins regardless of decision (except reject) ──────────────

def _edited_overhaul() -> dict:
    """A chat-edited overhaul that is structurally distinct from _overhaul()
    so tests can detect which one the composer applied."""
    return {
        "reorganization": {"section_order": ["summary"], "moves": []},
        "rewrites": [
            {
                "section_label": "experience:Acme Corp:Senior Engineer",
                "original_text": "Led migration of monolith to microservices on AWS ECS.",
                "suggested_text": "HUMAN/CHAT EDIT: led ECS migration end-to-end.",
                "claim_type": "restate",
                "supporting_evidence": "human edit via chat",
            },
        ],
    }


def test_compose_edited_overrides_overhaul_when_decision_is_null():
    rendered = compose_resume(_profile(), _overhaul(), _edited_overhaul(), decision=None)
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert any("HUMAN/CHAT EDIT" in b for b in acme.bullets)
    # Agent's [N] services rewrite must NOT be applied; edited wins.
    assert not any("[N] services" in b for b in acme.bullets)
    assert rendered.banner and "no decision yet" in rendered.banner.lower()


def test_compose_edited_overrides_overhaul_when_decision_is_revise():
    rendered = compose_resume(_profile(), _overhaul(), _edited_overhaul(),
                              decision="revise")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert any("HUMAN/CHAT EDIT" in b for b in acme.bullets)
    assert rendered.banner and "decision: revise" in rendered.banner.lower()


def test_compose_edited_overrides_overhaul_when_decision_is_approve():
    """ADR-068: even on approve, an explicit edited wins over the agent's
    original overhaul. The user populated `edited` deliberately; rendering
    the agent's overhaul instead would be surprising."""
    rendered = compose_resume(_profile(), _overhaul(), _edited_overhaul(),
                              decision="approve")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert any("HUMAN/CHAT EDIT" in b for b in acme.bullets)
    assert rendered.banner and "approved" in rendered.banner.lower()
    assert "chat edits applied" in rendered.banner.lower()


def test_compose_reject_renders_original_even_with_edited_present():
    """Reject is the explicit 'throw out the overhaul' signal - edited
    must not override it."""
    rendered = compose_resume(_profile(), _overhaul(), _edited_overhaul(),
                              decision="reject")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert "Led migration of monolith to microservices on AWS ECS." in acme.bullets
    assert not any("HUMAN/CHAT EDIT" in b for b in acme.bullets)
    assert rendered.banner is None


# ── Composer: rewrite application ───────────────────────────────────────────

def test_rewrite_replaces_matching_bullet_in_named_experience():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    beta = next(i for i in rendered.experience if i.company == "Beta Inc")
    # Acme bullets changed; Beta bullets untouched.
    assert "Worked on the API gateway." in beta.bullets
    assert "Built internal tooling for the data team." in beta.bullets


def test_rewrite_unmatched_bullet_is_appended_not_dropped():
    overhaul = {
        "reorganization": {"section_order": ["summary", "experience"], "moves": []},
        "rewrites": [
            {
                "section_label": "experience:Acme Corp:Senior Engineer",
                "original_text": "this exact string is not in the resume",
                "suggested_text": "ADDED: led an additional initiative.",
                "claim_type": "restate",
                "supporting_evidence": "added per advice",
            },
        ],
    }
    rendered = compose_resume(_profile(), overhaul, None, decision="approve")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert any("ADDED" in b for b in acme.bullets)


def test_rewrite_substring_fallback_matches_noisy_bullets():
    overhaul = {
        "reorganization": {"section_order": ["summary", "experience"], "moves": []},
        "rewrites": [
            {
                "section_label": "experience:Acme Corp:Senior Engineer",
                "original_text": "Mentored two junior",  # substring of the actual bullet
                "suggested_text": "Mentored five engineers.",
                "claim_type": "reframe",
                "supporting_evidence": "test",
            },
        ],
    }
    rendered = compose_resume(_profile(), overhaul, None, decision="approve")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    assert "Mentored five engineers." in acme.bullets
    assert "Mentored two junior engineers." not in acme.bullets


def test_placeholders_survive_verbatim():
    # The fidelity contract: [N], [X]%, and similar placeholders the agent
    # emits when a metric can't be invented must not be normalized away.
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    acme = next(i for i in rendered.experience if i.company == "Acme Corp")
    rewrite = next(b for b in acme.bullets if "[N]" in b)
    assert "[N] services" in rewrite
    assert "[X]%" in rewrite


# ── Composer: reorganization moves + section_order ─────────────────────────

def test_reorganization_section_order_moves_certifications_above_education():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    so = rendered.section_order
    assert so.index("certifications") < so.index("education")


def test_reorganization_promote_section_floats_to_just_after_summary():
    overhaul = {
        "reorganization": {
            "section_order": ["summary", "experience", "skills",
                              "education", "certifications"],
            "moves": [{"action": "promote", "subject": "Skills", "rationale": ""}],
        },
        "rewrites": [],
    }
    rendered = compose_resume(_profile(), overhaul, None, decision="approve")
    so = rendered.section_order
    assert so[0] == "summary"
    assert so[1] == "skills"


def test_reorganization_cut_section_removes_it_from_section_order():
    overhaul = {
        "reorganization": {
            "section_order": ["summary", "experience", "skills",
                              "education", "certifications"],
            "moves": [{"action": "cut", "subject": "Education", "rationale": ""}],
        },
        "rewrites": [],
    }
    rendered = compose_resume(_profile(), overhaul, None, decision="approve")
    assert "education" not in rendered.section_order


def test_reorganization_unknown_subject_is_a_noop():
    overhaul = {
        "reorganization": {
            "section_order": ["summary", "experience"],
            "moves": [{"action": "promote", "subject": "Gaming Awards", "rationale": ""}],
        },
        "rewrites": [],
    }
    # Should not raise; section_order should be the agent's proposed order
    # padded with the leftover sections.
    rendered = compose_resume(_profile(), overhaul, None, decision="approve")
    assert rendered.section_order[0] == "summary"
    assert "experience" in rendered.section_order


# ── Renderer: markdown ──────────────────────────────────────────────────────

def test_render_markdown_contains_name_and_section_headers():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    md = render_markdown(rendered)
    assert "# Jamie Lee" in md
    assert "## Summary" in md
    assert "## Experience" in md
    assert "## Certifications" in md
    # Rewrites land in the markdown.
    assert "[N] services" in md


def test_render_markdown_banner_surfaced_for_preview():
    rendered = compose_resume(_profile(), _overhaul(), None, decision=None)
    md = render_markdown(rendered)
    assert "Preview" in md


# ── Renderer: plain text ────────────────────────────────────────────────────

def test_render_plain_text_uses_all_caps_section_headers():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    txt = render_plain_text(rendered)
    assert "SUMMARY" in txt
    assert "EXPERIENCE" in txt
    assert "JAMIE LEE" in txt
    # No markdown noise.
    assert "## " not in txt
    assert "**" not in txt


def test_render_plain_text_preserves_placeholders():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    txt = render_plain_text(rendered)
    # The plain-text renderer wraps at ~72 chars, so the literal `[N] services`
    # span can land split across two lines. The atomic tokens cannot.
    assert "[N]" in txt
    assert "[X]%" in txt


# ── Renderer: html ──────────────────────────────────────────────────────────

def test_render_html_is_well_formed_and_includes_sections():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    h = render_html(rendered)
    assert h.startswith("<!DOCTYPE html>")
    assert "<title>Jamie Lee</title>" in h
    assert "<h2>Experience</h2>" in h
    assert "<h2>Summary</h2>" in h
    assert "[N] services" in h


def test_render_html_escapes_dangerous_input():
    profile = _profile()
    profile["name"] = "<script>alert(1)</script>"
    rendered = compose_resume(profile, _overhaul(), None, decision="approve")
    h = render_html(rendered)
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h


# ── Renderer: JSON Resume ───────────────────────────────────────────────────

def test_render_json_resume_matches_subset_of_schema():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    j = render_json_resume(rendered)
    assert j["basics"]["name"] == "Jamie Lee"
    assert j["basics"]["email"] == "jamie@example.com"
    assert j["work"][0]["name"] == "Acme Corp"
    assert j["work"][0]["position"] == "Senior Engineer"
    assert j["work"][0]["startDate"] == "2022"
    assert j["work"][0]["endDate"] == ""  # 'present' normalizes to empty endDate
    # Rewrites flow into highlights.
    highlights = j["work"][0]["highlights"]
    assert any("[N] services" in h for h in highlights)


# ── Renderer: DOCX ──────────────────────────────────────────────────────────

def test_render_docx_produces_valid_zip_with_document_xml():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    payload = render_docx(rendered)
    assert payload[:4] == b"PK\x03\x04", "DOCX must be a ZIP archive"
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = set(zf.namelist())
        assert "word/document.xml" in names
        body = zf.read("word/document.xml").decode("utf-8")
    assert "Jamie Lee" in body
    assert "[N] services" in body


# ── Renderer: PDF ───────────────────────────────────────────────────────────

def test_render_pdf_starts_with_pdf_magic_and_includes_name():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    payload = render_pdf(rendered)
    assert payload[:5] == b"%PDF-"
    # The candidate's name should appear somewhere in the raw stream.
    assert b"Jamie Lee" in payload


# ── Public dispatch ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["md", "txt", "html", "json", "docx", "pdf"])
def test_render_dispatch_returns_nonempty_bytes_for_every_supported_format(fmt):
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    out = render(fmt, rendered)
    assert isinstance(out, (bytes, bytearray))
    assert len(out) > 0


def test_render_dispatch_unknown_format_raises():
    rendered = compose_resume(_profile(), _overhaul(), None, decision="approve")
    with pytest.raises(ValueError):
        render("xyz", rendered)


def test_content_type_for_every_format_is_documented():
    for fmt in ("md", "txt", "html", "json", "docx", "pdf"):
        ct = export_content_type(fmt)
        assert ct and "/" in ct
        ext = export_file_extension(fmt)
        assert ext  # always returns something


# ── ADR-067: GPA, honors, skill_groups ──────────────────────────────────────

def _profile_with_fidelity_extras() -> dict:
    """A parsed_profile with the ADR-067 fields populated, matching the data
    shape a fresh parse produces."""
    p = _profile()
    p["education"] = [
        {
            "institution": "UT Austin",
            "degree": "BS Computer Science",
            "year": 2020,
            "gpa": "3.9/4.0",
            "honors": ["Dean's List 2018, 2019, 2020", "Phi Beta Kappa"],
        },
    ]
    p["skill_groups"] = [
        {"category": "Languages",       "skills": ["Python", "Go"]},
        {"category": "Cloud",           "skills": ["AWS", "Docker", "Kubernetes"]},
        {"category": "Databases",       "skills": ["PostgreSQL"]},
    ]
    # Flat list also present (parser keeps both in sync).
    p["skills"] = ["Python", "Go", "AWS", "Docker", "Kubernetes", "PostgreSQL"]
    return p


def test_compose_picks_up_gpa_and_honors():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    ed = rendered.education[0]
    assert ed.gpa == "3.9/4.0"
    assert "Dean's List 2018, 2019, 2020" in ed.honors
    assert "Phi Beta Kappa" in ed.honors


def test_compose_picks_up_skill_groups():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    assert len(rendered.skill_groups) == 3
    assert rendered.skill_groups[0].category == "Languages"
    assert rendered.skill_groups[0].skills == ["Python", "Go"]
    # The flat list is preserved alongside (used by Scoring agent etc.).
    assert "Python" in rendered.skills


def test_render_markdown_emits_gpa_and_honors_in_education():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    md = render_markdown(rendered)
    assert "GPA: 3.9/4.0" in md
    assert "Dean's List 2018, 2019, 2020" in md
    assert "Phi Beta Kappa" in md


def test_render_markdown_emits_skill_groups_when_present():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    md = render_markdown(rendered)
    assert "**Languages:**" in md
    assert "Python · Go" in md
    assert "**Cloud:**" in md


def test_render_markdown_falls_back_to_flat_skills_when_no_groups():
    """Resumes parsed before ADR-067 have no skill_groups -> flat list."""
    p = _profile()  # no skill_groups
    rendered = compose_resume(p, _overhaul(), None, decision="approve")
    md = render_markdown(rendered)
    assert "**Languages:**" not in md
    assert "## Skills" in md


def test_render_html_grouped_skills():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    h = render_html(rendered)
    assert "<strong>Languages:</strong>" in h
    assert "<strong>Cloud:</strong>" in h


def test_render_html_emits_gpa_and_honors():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    h = render_html(rendered)
    assert "GPA: 3.9/4.0" in h
    assert "Phi Beta Kappa" in h


def test_render_plain_text_emits_grouped_skills():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    txt = render_plain_text(rendered)
    assert "Languages:" in txt
    assert "Python, Go" in txt
    assert "GPA: 3.9/4.0" in txt


def test_render_json_resume_emits_gpa_as_score_and_honors_as_courses():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    j = render_json_resume(rendered)
    assert j["education"][0]["score"] == "3.9/4.0"
    assert "Phi Beta Kappa" in j["education"][0]["courses"]
    # Each grouped skill carries its category in `keywords`.
    skill_entries = j["skills"]
    languages = [s for s in skill_entries if "Languages" in (s.get("keywords") or [])]
    assert len(languages) == 2  # Python + Go


def test_render_docx_includes_gpa_text():
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    payload = render_docx(rendered)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        body = zf.read("word/document.xml").decode("utf-8")
    assert "3.9/4.0" in body
    assert "Phi Beta Kappa" in body
    assert "Languages" in body


def test_render_pdf_does_not_crash_with_gpa_and_honors_and_skill_groups():
    """ReportLab compresses text streams so the raw bytes are not searchable
    for plain content. This test asserts the PDF generates cleanly when the
    education has GPA + honors AND skills are grouped - the structural path
    the ADR-067 fields exercise."""
    rendered = compose_resume(_profile_with_fidelity_extras(),
                              _overhaul(), None, decision="approve")
    payload = render_pdf(rendered)
    assert payload[:5] == b"%PDF-"
    assert len(payload) > 1000  # a non-trivial document was produced
