"""Title-based seniority detection for discovery filtering (ADR-065 extension).

Deterministic, no-LLM. The body-based experience filter (`experience_filter.py`)
reads the JD text for "5+ years" - but aggregator snippets (Adzuna) are truncated
to ~500 chars, so the requirement is frequently past the cut and invisible to us
(see bugs/BUG-010). The TITLE is never truncated and usually states the level
outright ("... - Mid", "Analyst II", "Tier 3", "Senior SOC Analyst"). This module
flags a title as ABOVE entry level so an entry-targeting profile
(`search.exclude_senior`) can drop it regardless of whether the body survived
truncation.

Tuned for PRECISION with word boundaries so genuine entry titles are KEPT:
"Analyst I", "Tier 1", "Associate", "Junior", plain "SOC Analyst" -> False.
"""
from __future__ import annotations

import re

# Above-entry level signals, word-boundary anchored (so "lead" does not match
# "leadership", "ii" does not match "Hawaii", "mid" does not match "midwest").
# Ordered roughly senior -> mid -> numeric level.
_ABOVE_ENTRY = [
    r"senior", r"\bsr\.?\b", r"staff", r"principal", r"\bdistinguished\b",
    r"\blead(er)?\b", r"\bmanager\b", r"\bmgr\b", r"director", r"\bvp\b",
    r"vice president", r"architect", r"head of", r"\bchief\b",
    r"\bmid\b", r"mid[\s-]?level", r"journeyman", r"experienced", r"seasoned",
    r"\bii\b", r"\biii\b", r"\biv\b",                      # roman-numeral levels
    r"\btier\s*(?:2|3|4|ii|iii|iv)\b", r"\blevel\s*(?:2|3|4)\b",
]
_ABOVE_ENTRY_RE = re.compile("|".join(_ABOVE_ENTRY), re.IGNORECASE)


def title_is_above_entry(title: str | None) -> bool:
    """True when a job TITLE signals a level above entry (senior / staff / lead /
    mid / II-III-IV / tier 2+ ...). Deterministic; safe on None/empty (False).

    Precision-first: entry titles are kept - "Analyst I", "Tier 1", "Associate",
    "Junior Security Analyst", plain "SOC Analyst" all return False. Documented
    rare false positive: a regional "Mid-Atlantic ..." title (precision/recall
    trade-off deliberately favors dropping for an entry-only profile).
    """
    t = (title or "").strip()
    if not t:
        return False
    return _ABOVE_ENTRY_RE.search(t) is not None
