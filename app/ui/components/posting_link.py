"""Posting-link reliability helpers (ADR-093, feature #1).

A scored match is worthless if its apply link is dead. Aggregator links (Adzuna,
etc.) are redirect snippets that go stale; employer-direct ATS links (Greenhouse,
Lever) are the source of truth. These helpers let the UI:

  * badge a job's link source (employer-direct vs aggregator vs the user's own URL);
  * offer a deterministic "find the live posting" web-search fallback when the stored
    link is an aggregator/unknown source or the posting is stale (link likely dead).

No live link verification (intentionally - Adzuna 429s + JS-gates that path, see the
ADR-080/081 dead-link arc). Pure functions + one render helper; no LLM, no network.

Guardrail (CLAUDE.md no-application-tracking): this surfaces *navigation to the
posting* only - never an Apply/Save/applied status. UI text says "posting", never
"apply".
"""
from __future__ import annotations

import urllib.parse

import streamlit as st

# Employer-direct ATS feeds (source of truth, ADR-081) vs aggregators (redirect
# snippets that expire). Anything else (a user's custom URL) is "custom".
_DIRECT = {"greenhouse", "lever"}
_AGGREGATOR = {"adzuna", "indeed", "linkedin"}


def source_kind(source: str | None) -> str:
    """Classify a job's `source` into direct | aggregator | custom | unknown."""
    s = (source or "").strip().lower()
    if s in _DIRECT:
        return "direct"
    if s in _AGGREGATOR:
        return "aggregator"
    if s in ("custom_url", "custom"):
        return "custom"
    return "unknown"


def source_badge(source: str | None) -> str:
    """A short, human badge for the link source (empty when unknown)."""
    return {
        "direct": "🟢 Employer-direct",
        "aggregator": "🟡 Aggregator link",
        "custom": "🔗 Your link",
        "unknown": "",
    }.get(source_kind(source), "")


def live_search_url(title: str | None, company: str | None) -> str:
    """A web-search deep link to find the role on the employer's own site - the
    reliable fallback when the stored link is an expiring aggregator redirect."""
    query = " ".join(x for x in [(title or "").strip(), (company or "").strip(),
                                 "careers"] if x).strip() or "jobs"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


def needs_fallback(job: dict, *, stale: bool = False) -> bool:
    """True when the stored link is unreliable: missing, an aggregator/unknown
    source, or a stale posting (its redirect has likely expired)."""
    if not job.get("url"):
        return True
    if stale:
        return True
    return source_kind(job.get("source")) in ("aggregator", "unknown")


def render_posting_links(job: dict, *, key: str, stale: bool = False,
                         container_width: bool = True) -> None:
    """Render the 'open the posting' button plus a 'find the live posting' search
    fallback when the stored link is unreliable. `key` disambiguates the buttons."""
    url = job.get("url")
    if url:
        st.link_button("Open the posting ↗", url, use_container_width=container_width)
    if needs_fallback(job, stale=stale):
        st.link_button(
            "Find the live posting 🔎",
            live_search_url(job.get("title"), job.get("company")),
            use_container_width=container_width,
            help="Aggregator links expire; stale postings often 404. This searches "
                 "the web for the role on the employer's own site.",
        )
