"""Profile-derived location/country filter for discovery (ADR-103).

Deterministic, no LLM, no network, no geo dataset. Resolves a free-text location
string to a country, derives the profile's allowed country set from its OWN
configured `search.locations`, and drops postings confidently resolved to a
country the profile did not ask for.

The ATS-direct scrapers (ADR-081) return a company's entire GLOBAL board with no
location gate, so out-of-country postings (e.g. a US profile getting Ireland/UK
roles) leak in. Adzuna already filters country at query time; this is the uniform
gate for every source.

Conservative for dropping (never-lose-the-run): a posting whose country cannot be
confidently resolved -- an unparseable string, a bare "Remote", a US city with no
state token -- is KEPT. Only a confidently-foreign posting (relative to the
profile's countries) is dropped. Short aliases ("us", "uk") match only as whole
tokens, never as substrings, so "Houston" is not read as containing "us".
"""
from __future__ import annotations

import re
from typing import Iterable

# US: 50 states + DC, 2-letter codes and full names, plus country aliases.
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}
# Whole-segment / whole-token US aliases.
_US_ALIASES = {
    "united states", "united states of america", "usa", "u.s.a.", "u.s.", "us",
    "u.s", "america", "remote us", "us remote", "remote - us", "remote, us",
}

# Foreign countries -> ISO-2. Multi-word names are matched as substrings of a
# location segment; single short words ("uk", "uae") only as whole tokens. Keep
# this lexicon focused on the common cases; it is trivial to extend.
_COUNTRY_MULTIWORD = {
    "united kingdom": "GB", "great britain": "GB", "northern ireland": "GB",
    "republic of ireland": "IE", "new zealand": "NZ", "south africa": "ZA",
    "united arab emirates": "AE", "czech republic": "CZ", "saudi arabia": "SA",
    "south korea": "KR", "hong kong": "HK", "costa rica": "CR",
}
_COUNTRY_WORD = {
    "uk": "GB", "england": "GB", "scotland": "GB", "wales": "GB", "britain": "GB",
    "ireland": "IE", "canada": "CA", "india": "IN", "australia": "AU",
    "germany": "DE", "france": "FR", "netherlands": "NL", "holland": "NL",
    "spain": "ES", "italy": "IT", "poland": "PL", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "finland": "FI", "singapore": "SG", "japan": "JP",
    "china": "CN", "brazil": "BR", "mexico": "MX", "israel": "IL",
    "switzerland": "CH", "austria": "AT", "belgium": "BE", "portugal": "PT",
    "romania": "RO", "ukraine": "UA", "philippines": "PH", "argentina": "AR",
    "colombia": "CO", "chile": "CL", "czechia": "CZ", "hungary": "HU",
    "greece": "GR", "turkey": "TR", "egypt": "EG", "nigeria": "NG", "kenya": "KE",
    "pakistan": "PK", "bangladesh": "BD", "indonesia": "ID", "malaysia": "MY",
    "thailand": "TH", "vietnam": "VN", "uae": "AE", "estonia": "EE",
    "lithuania": "LT", "latvia": "LV", "bulgaria": "BG", "croatia": "HR",
    "serbia": "RS", "slovakia": "SK", "slovenia": "SI", "luxembourg": "LU",
}

_WORD_RE = re.compile(r"[a-z0-9.]+")


def country_of(location: str | None) -> str | None:
    """Resolve a free-text location to a country ("US" or an ISO-2), or None.

    None means "could not confidently resolve" -> the caller KEEPS the posting.
    Explicit foreign country names win over US state tokens so "Dublin, Ireland"
    -> IE while "Dublin, OH" -> US.
    """
    if not location:
        return None
    low = location.lower().strip()
    if not low:
        return None

    # Comma/slash/paren-delimited segments + a flat word set for token matching.
    segments = [s.strip() for s in re.split(r"[,/()|]", low) if s.strip()]
    words = set(_WORD_RE.findall(low))

    # 1. Explicit foreign country (multi-word substring, then single whole-word).
    for name, code in _COUNTRY_MULTIWORD.items():
        if name in low:
            return code
    for seg in segments:
        if seg in _COUNTRY_WORD:
            return _COUNTRY_WORD[seg]
    for word in words:
        if word in _COUNTRY_WORD:
            return _COUNTRY_WORD[word]

    # 2. Explicit US (aliases as whole segments/words, state names, state codes).
    if any(seg in _US_ALIASES for seg in segments) or (words & {"usa", "us", "u.s.", "u.s", "america"}):
        return "US"
    if any(seg in _US_STATE_NAMES for seg in segments):
        return "US"
    # 2-letter state code as an original-case token (e.g. ", GA" / "MD").
    for tok in re.split(r"[,\s/()|]+", location):
        if tok.strip().upper() in _US_STATE_CODES and tok.strip().isalpha() and len(tok.strip()) == 2:
            return "US"

    return None


def derive_allowed_countries(locations: Iterable[str] | None) -> set[str]:
    """The profile's allowed country set, derived from its own search.locations.

    Empty when no configured location resolves to a country (e.g. all "Remote"),
    which makes the filter a no-op (keep everything).
    """
    allowed: set[str] = set()
    for loc in locations or []:
        c = country_of(loc)
        if c:
            allowed.add(c)
    return allowed


def filter_by_location(postings: list, allowed: set[str]):
    """Drop postings confidently resolved to a country not in `allowed`.

    Returns (kept, dropped_count, samples). An empty `allowed` keeps everything
    (cannot scope). A posting whose country is None (ambiguous) is KEPT.
    Samples are PII-safe (title/company/location/url) and capped.
    """
    if not allowed:
        return postings, 0, []

    kept: list = []
    samples: list[dict] = []
    dropped = 0
    for p in postings:
        loc = getattr(p, "location", None)
        c = country_of(loc)
        if c is not None and c not in allowed:
            dropped += 1
            if len(samples) < 10:
                samples.append({
                    "title": getattr(p, "title", None),
                    "company": getattr(p, "company", None),
                    "location": loc,
                    "country": c,
                    "url": getattr(p, "url", None),
                })
            continue
        kept.append(p)
    return kept, dropped, samples
