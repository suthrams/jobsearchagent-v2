"""Security-clearance detection (ADR-094).

A deterministic, no-LLM predicate that flags a job posting as requiring a US (or
allied) government security clearance. Used by the relevance_filter node to drop
clearance-gated roles before scoring when the profile opts in
(search.exclude_clearance), so a candidate who can't/won't pursue cleared work
doesn't pay to score them.

Detection is keyword/phrase based and tuned for PRECISION over recall: it requires a
qualified phrase ("security clearance", "TS/SCI", "active Secret clearance",
"polygraph", ...) rather than the bare word "clearance" or "secret", so it does not
trip on "clearance sale" or "secret sauce" or the CompTIA "Security+" cert.

Aggregator caveat (bugs/BUG-010): Adzuna stores only a ~500-char snippet, so the
clearance sentence is frequently truncated away and body detection misses it. To
recover the most common cleared-government-SOC case, a small set of high-precision
TITLE signals ("watch floor", "watch officer", "SCIF", "cleared") is also matched -
the title is never truncated. This stays best-effort on aggregators (a generic
title with the requirement buried in the unseen body can still slip); ATS-direct
full text is where detection is reliable.
"""
from __future__ import annotations

import re

# High-precision clearance phrases. Each requires a qualifier so a bare "secret" or
# "clearance" can't false-positive. `[^.\n]{0,N}` keeps a match within one clause.
_PATTERNS = [
    r"security clearance",
    r"secret clearance",
    r"top[\s-]?secret",
    r"ts\s*/\s*sci", r"\bts[\s-]?sci\b", r"\btssci\b",
    r"\bsci\b[^.\n]{0,15}(clearance|eligib)",
    r"\bpolygraph\b",
    r"\b(dod|doe|government|federal|active|current|existing|interim)\s+(security\s+)?clearance",
    r"\bclearance\b[^.\n]{0,15}(required|mandatory|eligib)",
    r"(obtain|maintain|able to obtain)[^.\n]{0,25}clearance",
]
_CLEARANCE_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)

# Title-only signals for cleared government work (BUG-010). High precision: each is
# near-exclusively used by cleared DoD/IC roles. "watch floor"/"watch officer" are
# 24/7 cleared SOC operations; "SCIF" is a secure compartmented facility; a bare
# "cleared" in a title ("Cleared SOC Analyst") states it outright.
_TITLE_PATTERNS = [
    r"watch\s+floor", r"watch\s+officer", r"\bscif\b", r"\bcleared\b",
]
_CLEARANCE_TITLE_RE = re.compile("|".join(_TITLE_PATTERNS), re.IGNORECASE)


def requires_clearance(description: str | None, title: str | None = None) -> bool:
    """True when the posting's title or description signals a required security
    clearance. Deterministic; safe on None/empty (returns False).

    The body+title blob is matched against the full phrase set; the title is ALSO
    matched against the gov-SOC title signals (BUG-010) so a truncated aggregator
    snippet can't hide a cleared watch-floor role.
    """
    blob = f"{title or ''}\n{description or ''}"
    if blob.strip() and _CLEARANCE_RE.search(blob) is not None:
        return True
    return bool(title) and _CLEARANCE_TITLE_RE.search(title) is not None
