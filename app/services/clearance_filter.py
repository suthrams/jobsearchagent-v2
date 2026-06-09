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


def requires_clearance(description: str | None, title: str | None = None) -> bool:
    """True when the posting's title or description signals a required security
    clearance. Deterministic; safe on None/empty (returns False)."""
    blob = f"{title or ''}\n{description or ''}"
    if not blob.strip():
        return False
    return _CLEARANCE_RE.search(blob) is not None
