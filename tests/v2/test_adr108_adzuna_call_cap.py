"""Adzuna call-cap + partial-results tests (ADR-108).

Forcing functions for the regression the ADR-107 limiter exposed: a profile with a large
title x location grid (~209 calls) timed out the 180s discovery stage and lost ALL Adzuna
jobs. ADR-108 caps calls per run (interleaved sampling) and returns partial results on a
time budget instead of zero.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from app.services import rate_limiter as rl
from app.services.concurrent_adzuna_scraper import (
    ConcurrentAdzunaScraper,
    _interleave_tasks,
)


def _job(url):
    j = MagicMock()
    j.url = url
    return j


def _scraper(titles, locations, remote=None, *, max_calls_per_run=50,
             time_budget_s=150.0, fetch=None):
    rl._reset_adzuna_limiter_for_tests()
    v1 = MagicMock()
    v1.config.enabled = True
    v1.config.locations = locations
    v1.titles = titles
    v1.config.remote_keywords = remote or []
    v1.log_result = MagicMock()
    if fetch is not None:
        v1._fetch_jobs = fetch
    else:
        v1._fetch_jobs = MagicMock(side_effect=lambda kw, loc: [_job(f"{kw}|{loc}")])
    # limiter off (0) so timing is deterministic; the cap/budget logic is independent of it
    return ConcurrentAdzunaScraper(
        v1, max_workers=5, max_calls_per_minute=0,
        max_calls_per_run=max_calls_per_run, time_budget_s=time_budget_s,
    )


# ── interleave ──────────────────────────────────────────────────────────────────

def test_interleave_samples_both_dimensions():
    titles = [f"t{i}" for i in range(5)]
    locations = [f"l{j}" for j in range(5)]
    order = _interleave_tasks(titles, locations)
    assert len(order) == 25
    # The first 5 kept tasks must span multiple DISTINCT titles and locations -- not all
    # titles for one location (the naive nested-loop failure mode).
    prefix = order[:5]
    assert len({kw for kw, _ in prefix}) >= 3
    assert len({loc for _, loc in prefix}) >= 3


# ── call cap ────────────────────────────────────────────────────────────────────

def test_cap_truncates_calls():
    scraper = _scraper([f"t{i}" for i in range(19)],
                       [f"l{j}" for j in range(10)],
                       max_calls_per_run=50)
    scraper.scrape()
    # 19 x 10 = 190 tasks, capped to 50 actual fetches.
    assert scraper._scraper._fetch_jobs.call_count == 50


def test_cap_zero_is_uncapped():
    scraper = _scraper([f"t{i}" for i in range(19)],
                       [f"l{j}" for j in range(10)],
                       max_calls_per_run=0)
    scraper.scrape()
    assert scraper._scraper._fetch_jobs.call_count == 190


def test_small_grid_under_cap_runs_all():
    scraper = _scraper(["a", "b"], ["x"], remote=["c"], max_calls_per_run=50)
    scraper.scrape()
    # 2 titles x 1 location + 1 remote = 3 calls.
    assert scraper._scraper._fetch_jobs.call_count == 3


# ── partial results on budget ───────────────────────────────────────────────────

def test_partial_results_on_time_budget():
    # Fast tasks return immediately; slow tasks block past the budget. With a tiny budget
    # the scrape must return the fast jobs and not block on the slow ones.
    def fetch(kw, loc):
        if kw == "fast":
            return [_job(f"fast|{loc}")]
        # "slow" task blocks well past the time budget
        time.sleep(5.0)
        return [_job(f"slow|{loc}")]

    scraper = _scraper(["fast", "slow"], ["x"], max_calls_per_run=0,
                       time_budget_s=0.3, fetch=fetch)
    t0 = time.monotonic()
    jobs = scraper.scrape()
    elapsed = time.monotonic() - t0

    # Returned promptly (did not wait the full 5s for the slow task).
    assert elapsed < 3.0
    urls = {j.url for j in jobs}
    assert "fast|x" in urls
    assert "slow|x" not in urls
