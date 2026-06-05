"""ADR-080 surfacing: posted_at column + discovered-but-not-scored table helpers.

Pure UI helpers (formatting.py), so no Streamlit runtime is needed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.ui.formatting import (
    build_discovered_rows,
    discovery_funnel_summary,
    format_posting_age_short,
)

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_format_posting_age_short():
    assert format_posting_age_short("2026-06-01T00:00:00Z", now=NOW) == "3d"
    assert format_posting_age_short("2026-06-04T00:00:00Z", now=NOW) == "today"
    assert format_posting_age_short("2099-01-01T00:00:00Z", now=NOW) == "today"  # future clamps
    assert format_posting_age_short(None, now=NOW) == ""
    assert format_posting_age_short("garbage", now=NOW) == ""


def test_build_discovered_rows_flags_scored_and_unscored():
    discovered = [
        {"id": "j1", "title": "Eng", "company": "A", "location": "Remote",
         "posted_at": "2026-06-01T00:00:00Z"},
        {"id": "j2", "title": "Lead", "company": "B", "location": "NYC", "posted_at": None},
        {"id": "j3", "title": "Staff", "company": "C", "location": "SF",
         "posted_at": "2026-05-01T00:00:00Z"},
    ]
    scored = [
        {"job_id": "j1", "status": "scored"},
        {"job_id": "j3", "status": "budget_skipped"},
        # j2 never reached scoring
    ]
    rows = build_discovered_rows(discovered, scored, now=NOW)
    assert [r["Title"] for r in rows] == ["Eng", "Lead", "Staff"]
    assert rows[0]["Status"] == "✅ scored"
    assert rows[0]["Posted"] == "3d"
    assert rows[1]["Status"] == "not scored"   # no scored entry
    assert rows[1]["Posted"] == ""             # unknown date
    assert rows[2]["Status"] == "budget_skipped"


def test_build_discovered_rows_empty():
    assert build_discovered_rows([], [], now=NOW) == []
    assert build_discovered_rows(None, None, now=NOW) == []


def test_discovery_funnel_summary_lists_nonzero_drops():
    stats = {
        "title_filter_dropped": 2,
        "experience_filter_dropped": 0,
        "age_filter_dropped": 5,
        "relevance_dropped": 6,
        "dedup_total_dropped": 0,
        "max_jobs_truncated": 0,
    }
    out = discovery_funnel_summary(stats)
    assert out == "Filtered out before scoring — title 2, age 5, relevance 6"


def test_discovery_funnel_summary_empty_when_nothing_dropped():
    assert discovery_funnel_summary({}) == ""
    assert discovery_funnel_summary(None) == ""
    assert discovery_funnel_summary({"age_filter_dropped": 0}) == ""
