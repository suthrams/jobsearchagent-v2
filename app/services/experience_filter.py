"""Years-of-experience parsing for discovery filtering (ADR-065).

Deterministic, regex-based — no LLM cost. Parses a job description's free text for
the lowest stated experience requirement so a per-profile cap can drop roles that
ask for more than an early-career candidate has.

`min_required_years` returns the LOWEST numeric requirement it finds (the entry
bar), or 0 when an explicit early-career signal is present, or None when nothing
is detectable. None is treated as "keep" by the caller (do not penalize a silent
JD — mirrors salary's ignore_if_missing).
"""
from __future__ import annotations

import re

# Explicit early-career signals -> floor of 0 years.
_ENTRY_SIGNALS = (
    "entry level", "entry-level", "new grad", "new graduate", "recent graduate",
    "no experience required", "no prior experience", "0-2 years", "0 to 2 years",
)

# "5+ years", "3-5 years", "3 to 5 years", "minimum of 2 years", "at least 4 yrs",
# "2 years of experience". Captures the FIRST number (the low end of any range /
# the single stated value). Bounded 0-50 to avoid matching stray numerics.
_YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)


def _all_required_years(description: str | None) -> list[int]:
    """Every stated years-of-experience requirement (0 for entry signals)."""
    if not description:
        return []
    text = description.lower()
    found: list[int] = []
    if any(sig in text for sig in _ENTRY_SIGNALS):
        found.append(0)
    for m in _YEARS_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 50:
            found.append(n)
    return found


def min_required_years(description: str | None) -> int | None:
    """Lowest stated requirement (the JD's entry bar), or None if undetectable."""
    found = _all_required_years(description)
    return min(found) if found else None


def max_required_years(description: str | None) -> int | None:
    """Highest stated requirement (the JD's senior bar), or None if undetectable."""
    found = _all_required_years(description)
    return max(found) if found else None


def exceeds_cap(description: str | None, max_years: int) -> bool:
    """True when even the posting's LOWEST stated requirement is above max_years.

    Conservative for dropping (only drops when the entry bar already exceeds the
    cap). None (no detectable experience) -> False (kept), by design.
    """
    req = min_required_years(description)
    return req is not None and req > max_years


def below_floor(description: str | None, min_years: int) -> bool:
    """True when even the posting's HIGHEST stated requirement is below min_years.

    Conservative for dropping (only drops when the senior bar is still below the
    floor — so "2+ years, 5+ preferred" is kept by a min-5 floor). None (no
    detectable experience) -> False (kept), by design.
    """
    req = max_required_years(description)
    return req is not None and req < min_years
