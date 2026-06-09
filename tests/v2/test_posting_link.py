"""Unit tests for the posting-link reliability helpers (ADR-093 #1)."""
from app.ui.components.posting_link import (
    live_search_url,
    needs_fallback,
    source_badge,
    source_kind,
)


def test_source_kind_classifies():
    assert source_kind("greenhouse") == "direct"
    assert source_kind("Lever") == "direct"          # case-insensitive
    assert source_kind("adzuna") == "aggregator"
    assert source_kind("indeed") == "aggregator"
    assert source_kind("custom_url") == "custom"
    assert source_kind("") == "unknown"
    assert source_kind(None) == "unknown"


def test_source_badge_known_vs_unknown():
    assert "Employer" in source_badge("greenhouse")
    assert "Aggregator" in source_badge("adzuna")
    assert source_badge(None) == ""
    assert source_badge("weird-source") == ""


def test_live_search_url_encodes_and_targets_careers():
    u = live_search_url("Staff Engineer", "Acme Corp")
    assert u.startswith("https://www.google.com/search?q=")
    assert " " not in u                               # spaces are encoded
    assert "Staff" in u and "Acme" in u and "careers" in u


def test_live_search_url_handles_missing_fields():
    u = live_search_url(None, None)
    assert u.startswith("https://www.google.com/search?q=")


def test_needs_fallback_rules():
    assert needs_fallback({"url": None}) is True                       # no link
    assert needs_fallback({"url": "x", "source": "adzuna"}) is True    # aggregator
    assert needs_fallback({"url": "x", "source": "greenhouse"}) is False
    # a fresh employer-direct link is reliable, but a stale one is not
    assert needs_fallback({"url": "x", "source": "greenhouse"}, stale=True) is True
