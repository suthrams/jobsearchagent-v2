"""Unit tests for the Matches 'where to focus' ranking (ADR-093 #2)."""
import pandas as pd

from app.ui.views.matches import _focus_jobs


def _df(rows):
    return pd.DataFrame(rows)


def test_ranks_by_best_active_track_score():
    df = _df([
        {"job_id": "a", "technical_score": 90, "architecture_score": 10, "overall_score": 50, "posted_at": "2026-06-01"},
        {"job_id": "b", "technical_score": 40, "architecture_score": 95, "overall_score": 60, "posted_at": "2026-06-02"},
        {"job_id": "c", "technical_score": 70, "architecture_score": 70, "overall_score": 80, "posted_at": "2026-06-03"},
    ])
    # IC only -> best = technical_score: 90 (a), 70 (c), 40 (b)
    assert list(_focus_jobs(df, ["ic"], limit=3)["job_id"]) == ["a", "c", "b"]
    # add architect -> b's 95 is now the single strongest fit
    assert _focus_jobs(df, ["ic", "architect"], limit=1).iloc[0]["job_id"] == "b"


def test_freshness_breaks_score_ties():
    df = _df([
        {"job_id": "old", "technical_score": 80, "overall_score": 50, "posted_at": "2026-05-01"},
        {"job_id": "new", "technical_score": 80, "overall_score": 50, "posted_at": "2026-06-01"},
    ])
    assert list(_focus_jobs(df, ["ic"], limit=2)["job_id"]) == ["new", "old"]


def test_excludes_zero_and_null_scores():
    df = _df([
        {"job_id": "zero", "technical_score": 0, "overall_score": 0},
        {"job_id": "null", "technical_score": None, "overall_score": None},
        {"job_id": "good", "technical_score": 75, "overall_score": 75},
    ])
    assert list(_focus_jobs(df, ["ic"], limit=3)["job_id"]) == ["good"]


def test_limit_is_respected():
    df = _df([{"job_id": str(i), "technical_score": 50 + i, "overall_score": 50} for i in range(10)])
    assert len(_focus_jobs(df, ["ic"], limit=3)) == 3


def test_empty_input_returns_empty():
    assert _focus_jobs(pd.DataFrame(), ["ic"]).empty
    assert _focus_jobs(None, ["ic"]).empty
