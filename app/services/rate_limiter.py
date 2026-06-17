"""Thread-safe client-side rate limiter (ADR-107).

Built for the Adzuna scraper, which fans one API call per task across a thread pool and
has no per-minute governor — so a normal run bursts ~20 calls in seconds and trips
Adzuna's per-minute cap (the "20/25 hits per minute" alert). This limiter paces call
STARTS to a configurable budget so the cap is never breached, regardless of how many
worker threads or tasks are in flight.

Design: a **min-interval** limiter. ``acquire()`` blocks until the next evenly-spaced
slot (``interval = 60 / max_per_minute``), so starts never exceed ``max_per_minute`` in a
rolling minute and there is no initial burst. ``penalize()`` pushes the next slot further
out when a 429 is observed (honor-Retry-After). A process-global singleton
(``get_adzuna_limiter``) makes all Adzuna scraper instances — including concurrent
workflow runs in the same process — share ONE budget.

Uses ``time.monotonic`` so wall-clock changes never affect spacing. All sleeping happens
OUTSIDE the lock so a slow caller cannot block others from reserving their slots.
"""
from __future__ import annotations

import threading
import time

# Cap a single 429 back-off so a hostile/large Retry-After cannot stall a run unbounded.
_MAX_PENALTY_S = 90.0
# Back-off applied when a 429 is seen without a usable Retry-After header.
_DEFAULT_PENALTY_S = 30.0


class RateLimiter:
    """Min-interval limiter: at most ``max_per_minute`` ``acquire()`` starts per minute.

    Thread-safe. ``max_per_minute <= 0`` disables the limiter (``acquire`` is a no-op).
    """

    def __init__(self, max_per_minute: int, *, clock=time.monotonic, sleep=time.sleep) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._sleep = sleep
        self._interval = (60.0 / max_per_minute) if max_per_minute and max_per_minute > 0 else 0.0
        # Monotonic time of the next permitted start. 0.0 = "no call issued yet".
        self._next_allowed = 0.0

    @property
    def max_per_minute(self) -> int:
        return 0 if self._interval <= 0 else round(60.0 / self._interval)

    def acquire(self) -> float:
        """Block until the next permitted slot. Returns the seconds waited (0 if none)."""
        if self._interval <= 0:
            return 0.0
        with self._lock:
            now = self._clock()
            start = now if self._next_allowed <= 0 else max(now, self._next_allowed)
            self._next_allowed = start + self._interval
            wait = start - now
        if wait > 0:
            self._sleep(wait)
        return max(0.0, wait)

    def penalize(self, seconds: float) -> None:
        """Push the next permitted slot out by ``seconds`` (honor a 429 back-off).

        Idempotent-ish: only ever delays, never advances. Bounded by ``_MAX_PENALTY_S``.
        """
        if self._interval <= 0 or seconds <= 0:
            return
        seconds = min(float(seconds), _MAX_PENALTY_S)
        with self._lock:
            self._next_allowed = max(self._next_allowed, self._clock() + seconds)

    def tighten(self, max_per_minute: int) -> None:
        """Adopt a STRICTER rate if requested; never loosen.

        Lets a process-global singleton respect the most conservative budget any caller
        asks for, so concurrent runs with different configs cannot collectively breach
        the cap.
        """
        if not max_per_minute or max_per_minute <= 0:
            return
        new_interval = 60.0 / max_per_minute
        with self._lock:
            if new_interval > self._interval:
                self._interval = new_interval


# ── Process-global Adzuna limiter ────────────────────────────────────────────────
# One budget shared by every Adzuna scraper instance in the process (ADR-107). The
# Adzuna credentials are app-global (env vars), so the per-minute cap is app-global too.

_adzuna_lock = threading.Lock()
_adzuna_limiter: RateLimiter | None = None


def get_adzuna_limiter(max_per_minute: int) -> RateLimiter | None:
    """Return the shared Adzuna limiter, or ``None`` when disabled (``max_per_minute<=0``).

    First call creates the singleton; later calls only ever TIGHTEN it (never loosen),
    so the most conservative configured budget wins.
    """
    global _adzuna_limiter
    if not max_per_minute or max_per_minute <= 0:
        return None
    with _adzuna_lock:
        if _adzuna_limiter is None:
            _adzuna_limiter = RateLimiter(max_per_minute)
        else:
            _adzuna_limiter.tighten(max_per_minute)
        return _adzuna_limiter


def _reset_adzuna_limiter_for_tests() -> None:
    """Test hook: drop the process-global singleton so each test starts clean."""
    global _adzuna_limiter
    with _adzuna_lock:
        _adzuna_limiter = None
