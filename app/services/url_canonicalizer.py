"""Stable URL canonicalization for the discovery dedup path.

Background (2026-05-29). Adzuna's search API returns redirect URLs with a
rotating session token in the query string (?se=<token>&utm_*&v=...).
The ad ID is stable in the path (e.g. `/land/ad/5690461826`); the query
rotates per fetch. Without canonicalization, the same ad fetched twice
yields two different URLs, two different `jobs.id` rows, and two
different scored rows - because the discovery dedup layer compares URLs
verbatim. We discovered this when the cyber-grad profile kept finding
"the same job over and over" - it was actually different URLs for the
same ad each run.

This module strips the query and fragment from Adzuna URLs so the URL
becomes stable across fetches. Other sources pass through unchanged - we
only canonicalize when we know the source rotates tracking parameters.
Add a new branch here when another source (LinkedIn, etc.) is shown to
rotate query parameters in a way that defeats dedup.
"""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def canonicalize_url(url: str | None) -> str:
    """Return a stable URL suitable for dedup.

    For Adzuna URLs (host contains "adzuna"), strips the query and
    fragment, leaving only scheme://host/path. For all other URLs,
    returns the input unchanged. Returns "" for None / empty input -
    matches the previous behaviour of `getattr(v1_job, "url", "")` in
    `JobDiscoveryService.normalize`.
    """
    if not url:
        return ""
    try:
        parts = urlparse(url)
    except Exception:
        return url
    host = (parts.netloc or "").lower()
    if "adzuna" not in host:
        return url
    return urlunparse((parts.scheme, parts.netloc, parts.path, "", "", ""))
