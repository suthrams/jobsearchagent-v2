"""Posting-age helpers for the staleness signal + max-age filter (ADR-080).

Deterministic, no LLM, no network. Computes how old a posting is from its
`posted_at` (ISO 8601), so a per-profile cap can drop stale postings (which
strongly correlate with pulled requisitions / dead apply links) and the UI can
show "Posted N days ago".

Conservative for dropping: a posting with no parseable `posted_at` is treated as
"unknown age" and KEPT (mirrors the experience filter's "silent JD is not
penalized", ADR-065). `now` is injectable so callers/tests get deterministic
results without patching the clock.
"""
from __future__ import annotations

from datetime import datetime, timezone

# A posting older than this many days is flagged "stale" in the UI (reuses the
# v1 Job.is_stale notion). This is a DISPLAY threshold only; the drop threshold is
# the per-profile search.max_posting_age_days config (0/None = off).
STALE_AFTER_DAYS = 30


def parse_posted_at(posted_at: str | None) -> datetime | None:
    """Parse an ISO 8601 posted_at into an aware UTC datetime, or None.

    Accepts the trailing-Z form the scrapers emit (e.g. "2026-01-02T03:04:05.000Z").
    Returns None for None/empty/unparseable input (caller treats None as "keep").
    """
    if not posted_at:
        return None
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def posting_age_days(posted_at: str | None, *, now: datetime | None = None) -> int | None:
    """Age of the posting in whole days, or None if `posted_at` is unparseable.

    Clamped at 0 (a future-dated posting reads as 0 days old, not negative).
    """
    dt = parse_posted_at(posted_at)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, (now - dt).days)


def is_older_than(posted_at: str | None, max_days: int | None, *, now: datetime | None = None) -> bool:
    """True only when a PARSEABLE posted_at is strictly older than max_days.

    `max_days` of 0/None disables the check (returns False). Undetectable age
    (None/unparseable posted_at) returns False -> the caller keeps the posting.
    """
    if not max_days or max_days <= 0:
        return False
    age = posting_age_days(posted_at, now=now)
    return age is not None and age > max_days


def is_stale(posted_at: str | None, *, now: datetime | None = None) -> bool:
    """Display helper: True when the posting is older than STALE_AFTER_DAYS.

    Unknown age -> False (no badge), so we never imply staleness we can't show.
    """
    age = posting_age_days(posted_at, now=now)
    return age is not None and age > STALE_AFTER_DAYS
