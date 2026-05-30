"""Unit tests for app/ui/formatting.py - the pure formatters extracted from
streamlit_app.py in Phase 1 of the UI refactor (docs/architecture/ui_refactor_plan.md).

These are the first automated tests over UI code. The functions are pure, so they
test directly with no Streamlit runtime.
"""
from __future__ import annotations

import pandas as pd

from app.ui import formatting as f


def test_fmt_ts_truncates_iso_and_handles_missing():
    assert f._fmt_ts("2026-05-30T11:41:44.123Z") == "2026-05-30 11:41:44"
    assert f._fmt_ts("2026-05-30") == "2026-05-30"
    assert f._fmt_ts(None) == "—"
    assert f._fmt_ts(float("nan")) == "—"


def test_score_badge_thresholds():
    assert f.score_badge(None) == "—"
    assert f.score_badge(80).endswith("80") and "🟢" in f.score_badge(80)
    assert "🟡" in f.score_badge(65)
    assert "🟠" in f.score_badge(50)
    assert "🔴" in f.score_badge(49)


def test_checked():
    assert f._checked(True) == "✅"
    assert f._checked(1) == "✅"
    assert f._checked(False) == "—"
    assert f._checked(None) == "—"
    assert f._checked(pd.NA) == "—"


def test_get_nested():
    d = {"a": {"b": {"c": 7}}}
    assert f._get_nested(d, ["a", "b", "c"]) == 7
    assert f._get_nested(d, ["a", "x"]) is None
    assert f._get_nested(d, ["a", "b", "c", "d"]) is None  # cannot descend past a scalar
    assert f._get_nested({}, ["a"]) is None


def test_label_with_cost():
    entries = [{"id": "m1", "input_per_m": 1.0, "output_per_m": 5.0}]
    assert f._label_with_cost("m1", entries) == "m1  ·  $1.00/M in · $5.00/M out"
    assert f._label_with_cost("unknown", entries) == "unknown"  # passthrough


def test_friendly_stage():
    assert f._friendly_stage("scoring") == "Scoring jobs"
    assert f._friendly_stage(None) == "—"
    assert f._friendly_stage("some_new_step") == "Some New Step"  # title-cased fallback


def test_safe_int_coerces_dataframe_origin_values():
    assert f._safe_int(None) == 0
    assert f._safe_int(float("nan")) == 0
    assert f._safe_int("") == 0
    assert f._safe_int("12") == 12
    assert f._safe_int(3.9) == 3
    assert f._safe_int(None, default=-1) == -1


def test_word_count():
    assert f._word_count("one two three") == 3
    assert f._word_count("") == 0
    assert f._word_count(None) == 0


def test_tokenize_keeps_hyphens_drops_punctuation():
    toks = f._tokenize("Multi-Region, high-availability! (p99)")
    assert "multi-region" in toks
    assert "high-availability" in toks
    assert "p99" in toks
    assert f._tokenize(None) == set()


def test_estimate_track_impact_classifies_added_tokens():
    draft = {
        "experience_bullet_suggestions": [
            # adds 'kubernetes' (technical) vs the original
            {"claim_type": "reword", "original_text": "ran services",
             "suggested_text": "ran services on kubernetes"},
            {"claim_type": "remove", "original_text": "old line", "suggested_text": ""},
            {"claim_type": "gap", "original_text": "", "suggested_text": "led a team"},
        ],
        "skills_section_suggestions": ["terraform"],
    }
    out = f._estimate_track_impact(draft)
    assert "kubernetes" in out["technical"]["added"]
    assert "terraform" in out["technical"]["added"]
    assert out["technical"]["signal"] in ("small_lift", "likely_lift")
    assert out["leadership"]["signal"] == "neutral"  # 'led a team' was a gap, not a reword
    assert out["freed_bullets"] == 1
    assert out["open_gaps"] == 1


def test_section_display_variants():
    assert f._section_display("headline", {}) == "Headline (positioning tagline)"
    assert f._section_display("summary", {}) == "Summary"
    assert f._section_display("skills", {}) == "Skills"
    assert f._section_display("experience:Acme:Staff Engineer", {}) == "Experience — Staff Engineer @ Acme"
    assert f._section_display("education:MIT", {}) == "Education — MIT"
    assert f._section_display("certifications:CKA", {}) == "Certifications — CKA"
    assert f._section_display("", {}) == "Other suggestions"
    assert f._section_display("weird", {}) == "weird"


def test_section_order_follows_resume_shape():
    profile = {
        "experience": [{"company": "Acme", "title": "Eng"}, {"company": "", "title": "skip"}],
        "education": [{"institution": "MIT"}],
        "certifications": [{"name": "CKA"}],
    }
    order = f._section_order(profile)
    assert order == [
        "headline", "summary",
        "experience:Acme:Eng",   # the entry with blank company is skipped
        "skills",
        "education:MIT",
        "certifications:CKA",
    ]
    assert f._section_order(None) == ["headline", "summary", "skills"]
