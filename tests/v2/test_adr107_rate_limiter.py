"""Adzuna client-side rate limiter tests (ADR-107).

Covers the pure limiter (even spacing under a mocked clock, penalize, tighten, disabled),
the process-global Adzuna singleton, and the scraper wiring (acquire per task + 429
penalize). The reported failure mode -- ~20 concurrent calls bursting past Adzuna's 25/min
cap -- becomes the forcing function: the limiter must pace call starts to the budget.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.rate_limiter import (
    RateLimiter,
    get_adzuna_limiter,
    _reset_adzuna_limiter_for_tests,
)


class _FakeClock:
    """Deterministic monotonic clock; sleep() advances it so spacing is testable."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture(autouse=True)
def _reset_singleton():
    _reset_adzuna_limiter_for_tests()
    yield
    _reset_adzuna_limiter_for_tests()


# ── pure limiter ────────────────────────────────────────────────────────────────

def test_first_acquire_does_not_wait():
    clk = _FakeClock()
    limiter = RateLimiter(20, clock=clk.now, sleep=clk.sleep)
    assert limiter.acquire() == 0.0
    assert clk.sleeps == []


def test_spacing_is_60_over_rate():
    clk = _FakeClock()
    limiter = RateLimiter(20, clock=clk.now, sleep=clk.sleep)  # interval = 3.0s
    limiter.acquire()                 # t=1000, no wait
    waited = limiter.acquire()        # must wait to t=1003
    assert waited == pytest.approx(3.0)
    assert clk.sleeps == [pytest.approx(3.0)]


def test_no_wait_when_caller_is_already_slow():
    clk = _FakeClock()
    limiter = RateLimiter(20, clock=clk.now, sleep=clk.sleep)  # interval 3.0s
    limiter.acquire()
    clk.t += 10.0  # ten seconds pass (e.g. a slow HTTP response elsewhere)
    assert limiter.acquire() == 0.0  # slot already in the past -> no throttle


def test_max_per_minute_zero_disables():
    clk = _FakeClock()
    limiter = RateLimiter(0, clock=clk.now, sleep=clk.sleep)
    for _ in range(50):
        assert limiter.acquire() == 0.0
    assert clk.sleeps == []
    assert limiter.max_per_minute == 0


def test_penalize_pushes_next_slot_out():
    clk = _FakeClock()
    limiter = RateLimiter(20, clock=clk.now, sleep=clk.sleep)  # interval 3.0s
    limiter.acquire()                 # t=1000
    limiter.penalize(30)              # next slot now >= t+30 = 1030
    waited = limiter.acquire()
    assert waited == pytest.approx(30.0)


def test_penalize_is_capped():
    clk = _FakeClock()
    limiter = RateLimiter(20, clock=clk.now, sleep=clk.sleep)
    limiter.penalize(99999)           # absurd Retry-After
    waited = limiter.acquire()
    assert waited <= 90.0 + 1e-6      # _MAX_PENALTY_S


def test_penalize_noop_when_disabled():
    clk = _FakeClock()
    limiter = RateLimiter(0, clock=clk.now, sleep=clk.sleep)
    limiter.penalize(30)
    assert limiter.acquire() == 0.0


def test_tighten_only_lowers_rate():
    limiter = RateLimiter(20)         # interval 3.0
    limiter.tighten(60)               # interval 1.0 -> looser; must be ignored
    assert limiter.max_per_minute == 20
    limiter.tighten(10)               # interval 6.0 -> stricter; adopted
    assert limiter.max_per_minute == 10


# ── process-global singleton ────────────────────────────────────────────────────

def test_get_adzuna_limiter_is_shared_singleton():
    a = get_adzuna_limiter(20)
    b = get_adzuna_limiter(20)
    assert a is b


def test_get_adzuna_limiter_tightens_to_strictest():
    a = get_adzuna_limiter(20)
    b = get_adzuna_limiter(10)        # stricter
    assert a is b
    assert a.max_per_minute == 10


def test_get_adzuna_limiter_zero_returns_none():
    assert get_adzuna_limiter(0) is None


# ── scraper wiring ──────────────────────────────────────────────────────────────

def _make_scraper(monkeypatch, max_calls_per_minute=20):
    """Build a ConcurrentAdzunaScraper around a fake v1 scraper (no network)."""
    from app.services import concurrent_adzuna_scraper as mod

    v1 = MagicMock()
    v1.config.enabled = True
    v1.config.locations = ["Atlanta, GA"]
    v1.titles = ["security analyst", "soc analyst"]
    v1.config.remote_keywords = ["security analyst"]
    v1._fetch_jobs = MagicMock(return_value=[])
    v1.log_result = MagicMock()
    return mod.ConcurrentAdzunaScraper(v1, max_workers=2,
                                       max_calls_per_minute=max_calls_per_minute)


def test_scrape_acquires_once_per_task(monkeypatch):
    scraper = _make_scraper(monkeypatch)
    # Replace the shared limiter with a spy that never sleeps.
    spy = MagicMock()
    spy.acquire.return_value = 0.0
    scraper._limiter = spy

    scraper.scrape()

    # tasks = locations(1) x titles(2) + remote_keywords(1) = 3
    assert spy.acquire.call_count == 3
    assert scraper._scraper._fetch_jobs.call_count == 3


def test_scrape_penalizes_on_429(monkeypatch):
    import httpx

    scraper = _make_scraper(monkeypatch)
    spy = MagicMock()
    spy.acquire.return_value = 0.0
    scraper._limiter = spy

    resp = httpx.Response(429, headers={"retry-after": "12"}, request=httpx.Request("GET", "https://x"))
    err = httpx.HTTPStatusError("429", request=resp.request, response=resp)
    scraper._scraper._fetch_jobs = MagicMock(side_effect=err)

    scraper.scrape()

    assert spy.penalize.called
    # The Retry-After value (12s) is what we backed off by.
    assert spy.penalize.call_args.args[0] == pytest.approx(12.0)


def test_disabled_limiter_scrape_still_works(monkeypatch):
    scraper = _make_scraper(monkeypatch, max_calls_per_minute=0)
    assert scraper._limiter is None
    # Must not raise with no limiter.
    scraper.scrape()
    assert scraper._scraper._fetch_jobs.call_count == 3
