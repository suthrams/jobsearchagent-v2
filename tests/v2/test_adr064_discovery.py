"""ADR-064: per-profile search criteria drive discovery + role-derived relevance."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.concurrent_adzuna_scraper import relevance_tokens
from app.services.job_discovery_service import JobDiscoveryService
from app.workflows.nodes.discover_jobs import make_discover_jobs_node


# ── relevance_tokens ──────────────────────────────────────────────────────────

def test_relevance_tokens_extracts_words_minus_stopwords():
    toks = relevance_tokens(["Security Analyst", "SOC Analyst", "Head of Security"])
    assert "security" in toks and "analyst" in toks and "soc" in toks
    assert "of" not in toks            # stopword dropped
    assert toks == list(dict.fromkeys(toks))  # de-duped, order-preserving


def test_relevance_tokens_empty():
    assert relevance_tokens([]) == []
    assert relevance_tokens(None) == []


# ── AdzunaScraper relevance gate is overridable (role-derived) ────────────────

def test_adzuna_relevance_override_lets_entry_level_cyber_pass(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    from scrapers.adzuna import AdzunaScraper
    from models.config_schema import AdzunaConfig

    cfg = AdzunaConfig(enabled=True, country="us", locations=["Atlanta, GA"])

    senior = AdzunaScraper(cfg, ["software architect"])          # default senior gate
    assert senior._is_relevant_title("Security Analyst") is False  # "analyst" not senior

    cyber = AdzunaScraper(cfg, ["Security Analyst"],
                          relevant_keywords=relevance_tokens(["Security Analyst", "SOC Analyst"]))
    assert cyber._is_relevant_title("SOC Analyst") is True
    assert cyber._is_relevant_title("Cybersecurity Analyst") is True


# ── discover() skip_builtin_adzuna ────────────────────────────────────────────

class ConcurrentAdzunaScraper:  # name matches the type-name check in discover()
    def scrape(self):
        return []


class _OtherScraper:
    def scrape(self):
        return []


def test_discover_skips_builtin_adzuna_when_flagged():
    svc = JobDiscoveryService(MagicMock(), {"search": {"max_jobs": 50}},
                              scrapers=[ConcurrentAdzunaScraper(), _OtherScraper()])
    svc.deduplicate = lambda p: p  # no DB
    # With the flag, the built-in Adzuna is dropped; only _OtherScraper runs.
    captured = []
    orig = svc.normalize
    svc.normalize = lambda job, wf: orig(job, wf)
    # Both scrapers return [], so we assert via the scraper set indirectly:
    # patch scrape to mark which ran.
    ran = []
    svc._scrapers[0].scrape = lambda: ran.append("adzuna") or []
    svc._scrapers[1].scrape = lambda: ran.append("other") or []
    svc.discover("wf", {}, skip_builtin_adzuna=True)
    assert ran == ["other"]
    ran.clear()
    svc.discover("wf", {}, skip_builtin_adzuna=False)
    assert set(ran) == {"adzuna", "other"}


# ── discover_jobs node builds a per-run Adzuna from roles ─────────────────────

def _node_with(capture: dict, factory):
    svc = MagicMock(spec=JobDiscoveryService)
    def _discover_with_stats(workflow_id, search_criteria, extra_scrapers=None,
                             skip_builtin_adzuna=False, max_years_experience=None,
                             min_years_experience=None, max_posting_age_days=None,
                             drop_dead_links=False, user_id=None):
        capture["extra"] = list(extra_scrapers or [])
        capture["skip"] = skip_builtin_adzuna
        capture["max_years"] = max_years_experience
        capture["drop_dead_links"] = drop_dead_links
        capture["user_id"] = user_id
        return [], {}
    svc.discover_with_stats.side_effect = _discover_with_stats
    return make_discover_jobs_node(svc, MagicMock(), MagicMock(),
                                   adzuna_scraper_factory=factory)


def test_node_builds_per_run_adzuna_when_roles_present():
    sentinel = object()
    calls = []
    factory = lambda roles, locations, exclude_senior=False: (calls.append((roles, locations)) or sentinel)
    cap: dict = {}
    node = _node_with(cap, factory)
    node({"workflow_id": "wf",
          "search_criteria": {"roles": ["Security Analyst"], "locations": ["Atlanta, GA", "Remote"]}})
    assert calls == [(["Security Analyst"], ["Atlanta, GA", "Remote"])]
    assert sentinel in cap["extra"]
    assert cap["skip"] is True


def test_node_falls_back_when_no_roles():
    factory = MagicMock()
    cap: dict = {}
    node = _node_with(cap, factory)
    node({"workflow_id": "wf", "search_criteria": {}})
    factory.assert_not_called()
    assert cap["skip"] is False


def test_node_honors_titles_alias_for_roles():
    sentinel = object()
    factory = lambda roles, locations, exclude_senior=False: sentinel
    cap: dict = {}
    node = _node_with(cap, factory)
    node({"workflow_id": "wf", "search_criteria": {"titles": ["Principal Engineer"]}})
    assert cap["skip"] is True
    assert sentinel in cap["extra"]


# ── SENIOR_TERMS split (2026-05-29 tuning) ────────────────────────────────────

def test_senior_terms_api_exclude_is_narrower_than_local_gate():
    """The API what_exclude list MUST be narrower than the local title gate.
    Adzuna matches what_exclude against title + description, so polysemic
    words ("manager", "lead", "staff", "head", "architect") would nuke
    entry-level postings whose descriptions just reference team structure.
    The local title gate is fine with those terms because it's substring-
    matched against the TITLE only."""
    from app.services.concurrent_adzuna_scraper import (
        SENIOR_TERMS, SENIOR_TERMS_API_EXCLUDE,
    )
    assert len(SENIOR_TERMS_API_EXCLUDE) < len(SENIOR_TERMS), \
        "API exclude list should be strictly narrower than local title gate"
    for narrow_term in SENIOR_TERMS_API_EXCLUDE:
        assert narrow_term in SENIOR_TERMS, \
            f"{narrow_term!r} in API list must also be in local title gate"
    polysemic = {"manager", "lead", "staff", "head of", "architect", "vice president"}
    for term in polysemic:
        assert term not in SENIOR_TERMS_API_EXCLUDE, \
            (f"{term!r} is polysemic in description text and should not be in "
             "the API what_exclude list")
        assert term in SENIOR_TERMS, \
            f"{term!r} must remain in the local title gate"


def test_local_title_gate_still_drops_senior_titles_after_split():
    """Regression check: even though we narrowed the API list, the local
    title gate must still reject "Senior X" and "X Lead" style titles when
    exclude_senior=True (the whole point of the option)."""
    from scrapers.adzuna import AdzunaScraper
    from models.config_schema import AdzunaConfig
    from models.filters import EXCLUDED_TITLE_KEYWORDS
    from app.services.concurrent_adzuna_scraper import (
        SENIOR_TERMS, relevance_tokens,
    )
    import os
    os.environ.setdefault("ADZUNA_APP_ID", "x")
    os.environ.setdefault("ADZUNA_APP_KEY", "y")
    cfg = AdzunaConfig(enabled=True, country="us", locations=["Atlanta, GA"])
    excluded_kw = list(EXCLUDED_TITLE_KEYWORDS) + SENIOR_TERMS
    scraper = AdzunaScraper(
        cfg, ["Security Analyst"],
        relevant_keywords=relevance_tokens(["Security Analyst", "SOC Analyst"]),
        excluded_keywords=excluded_kw,
    )
    assert scraper._is_relevant_title("Security Analyst") is True
    assert scraper._is_relevant_title("SOC Analyst") is True
    assert scraper._is_relevant_title("Senior Security Analyst") is False  # senior in title
    assert scraper._is_relevant_title("Security Team Lead") is False       # lead in title
    assert scraper._is_relevant_title("Information Security Manager") is False
    assert scraper._is_relevant_title("Principal Security Engineer") is False


def test_adzuna_scraper_joins_what_exclude_with_spaces():
    """Adzuna's what_exclude takes a single space-separated string. The v1
    scraper joins the list via " ". With the narrow list this produces a
    short string of high-precision tokens. Catches any future refactor
    that switches to comma separators (which Adzuna does not accept)."""
    from scrapers.adzuna import AdzunaScraper
    from models.config_schema import AdzunaConfig
    from app.services.concurrent_adzuna_scraper import SENIOR_TERMS_API_EXCLUDE
    import os
    os.environ.setdefault("ADZUNA_APP_ID", "x")
    os.environ.setdefault("ADZUNA_APP_KEY", "y")
    cfg = AdzunaConfig(enabled=True, country="us", locations=["Atlanta, GA"])
    scraper = AdzunaScraper(
        cfg, ["Security Analyst"],
        what_exclude=SENIOR_TERMS_API_EXCLUDE,
    )
    assert scraper._what_exclude == "senior principal vp director"
