"""ADR-101: Workday ATS-direct scraper.

Covers the genuinely-new shapes vs Greenhouse/Lever:
  * the 3-part career-URL parser + its SSRF host guard (forcing function - the parser
    is the ONLY place a user URL becomes a request target);
  * the two-phase list -> title-filter -> capped detail fetch (the title gate must run
    BEFORE any detail fetch, the load-bearing volume control);
  * never-lose-the-run: a failed board / failed detail is skipped, never raised;
  * the seam wiring (build_ats_scrapers, verify_ats_board) through the workday branch.

No network: httpx is patched.
"""
from __future__ import annotations

import re

import httpx
import pytest

from app.services import workday_scraper as wd
from app.services.ats_scrapers import build_ats_scrapers, verify_ats_board
from app.services.workday_scraper import (
    WorkdayScraper,
    _normalize_boards,
    _parse_relative_posted,
    parse_workday_url,
    verify_workday_board,
)
from models.job import JobSource


# ── parse_workday_url: valid forms + the SSRF host guard (forcing function) ───

@pytest.mark.parametrize("url, expected", [
    ("https://leidos.wd5.myworkdayjobs.com/en-US/External", ("leidos", "wd5", "External")),
    ("https://bah.wd1.myworkdayjobs.com/BAH_Jobs", ("bah", "wd1", "BAH_Jobs")),
    ("https://gdit.wd5.myworkdayjobs.com/External_Career_Site", ("gdit", "wd5", "External_Career_Site")),
    ("leidos.wd5.myworkdayjobs.com/External", ("leidos", "wd5", "External")),  # no scheme
    ("https://Leidos.WD5.MyWorkdayJobs.com/External", ("leidos", "wd5", "External")),  # case
])
def test_parse_workday_url_valid(url, expected):
    assert parse_workday_url(url) == expected


@pytest.mark.parametrize("url", [
    None, "", "   ",
    "https://evil.com/External",                       # not a workday host
    "https://leidos.wd5.notmyworkdayjobs.com/External",  # lookalike host
    "https://myworkdayjobs.com/External",              # missing tenant/dc labels
    "https://leidos.wd5.myworkdayjobs.com.evil.com/X",  # suffix-append attack
    "ftp://leidos.wd5.myworkdayjobs.com/External",     # non-http scheme
    "javascript:alert(1)",
    "https://leidos.wd5.myworkdayjobs.com/",           # no site segment
    "https://leidos.wd5.myworkdayjobs.com/en-US",      # only a locale, no site
])
def test_parse_workday_url_rejects_non_workday_and_malformed(url):
    """The host guard is the SSRF control - anything not tenant.dc.myworkdayjobs.com
    (or otherwise unparseable) returns None and is never fetched."""
    assert parse_workday_url(url) is None


def test_parse_relative_posted():
    assert _parse_relative_posted("Posted Today") is not None
    assert _parse_relative_posted("Posted Yesterday") is not None
    assert _parse_relative_posted("Posted 5 Days Ago") is not None
    assert _parse_relative_posted("Posted 30+ Days Ago") is not None
    assert _parse_relative_posted("Posted 2 Months Ago") is not None
    assert _parse_relative_posted("whenever") is None
    assert _parse_relative_posted(None) is None


def test_normalize_boards_drops_malformed():
    out = _normalize_boards([
        {"tenant": "a", "dc": "wd1", "site": "X"},
        ["b", "wd2", "Y"],
        {"tenant": "", "dc": "wd", "site": "Z"},  # incomplete -> dropped
        "junk",                                    # wrong type -> dropped
    ])
    assert out == [("a", "wd1", "X"), ("b", "wd2", "Y")]


# ── fake CXS client (list POST + detail GET), keyed by tenant in the URL ───────

class _FakeClient:
    def __init__(self, boards, *, fail_list=(), fail_detail=()):
        # boards: {tenant: {"postings": [...], "total": int, "details": {ep: info}}}
        self.boards = boards
        self.fail_list = set(fail_list)
        self.fail_detail = set(fail_detail)
        self.post_calls = []
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _tenant(self, url):
        return re.search(r"/wday/cxs/([^/]+)/", url).group(1)

    def post(self, url, json=None):
        self.post_calls.append((url, json))
        tenant = self._tenant(url)
        if tenant in self.fail_list:
            raise httpx.ConnectError("boom", request=httpx.Request("POST", url))
        b = self.boards[tenant]
        off, lim = json.get("offset", 0), json.get("limit", 20)
        page = b["postings"][off:off + lim]
        return httpx.Response(200, json={"total": b.get("total", len(b["postings"])),
                                         "jobPostings": page},
                              request=httpx.Request("POST", url))

    def get(self, url):
        self.get_calls.append(url)
        tenant = self._tenant(url)
        ep = re.search(r"/wday/cxs/[^/]+/[^/]+(/.*)$", url).group(1)
        if ep in self.fail_detail:
            raise httpx.ConnectError("boom", request=httpx.Request("GET", url))
        info = self.boards[tenant]["details"].get(ep, {})
        return httpx.Response(200, json={"jobPostingInfo": info},
                              request=httpx.Request("GET", url))


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr(wd.httpx, "Client", lambda *a, **k: fake)


# ── two-phase fetch: title-filter BEFORE detail fetch + full JD mapping ───────

def test_scrape_title_filters_before_detail_fetch(monkeypatch):
    boards = {"leidos": {
        "postings": [
            {"title": "Senior Software Engineer", "externalPath": "/job/se",
             "locationsText": "Reston, VA", "postedOn": "Posted 3 Days Ago"},
            {"title": "Cyber Analyst", "externalPath": "/job/cy",
             "locationsText": "Reston, VA", "postedOn": "Posted Today"},
            {"title": "Janitor", "externalPath": "/job/jan",  # no relevant token
             "locationsText": "Reston, VA", "postedOn": "Posted 1 Day Ago"},
        ],
        "details": {
            "/job/se": {"jobDescription": "<p>Build <b>software</b>. TS/SCI required.</p>"},
            "/job/cy": {"jobDescription": "<p>Analyze threats.</p>"},
        },
    }}
    fake = _FakeClient(boards)
    _patch_client(monkeypatch, fake)

    scraper = WorkdayScraper([{"tenant": "leidos", "dc": "wd5", "site": "External"}],
                             roles=["Software Engineer"],
                             relevant_tokens=["software", "cyber"])
    jobs = scraper.scrape()

    # Janitor is title-filtered out BEFORE any detail fetch -> only 2 detail GETs.
    assert len(fake.get_calls) == 2
    assert {j.title for j in jobs} == {"Senior Software Engineer", "Cyber Analyst"}
    se = next(j for j in jobs if j.title.startswith("Senior"))
    assert se.source == JobSource.WORKDAY
    assert se.company == "leidos"
    assert "TS/SCI required" in se.description            # full JD, HTML stripped
    assert "<b>" not in se.description
    assert se.url == "https://leidos.wd5.myworkdayjobs.com/External/job/se"
    assert se.posted_at is not None                       # relative date parsed


def test_scrape_skips_failed_detail_keeps_rest(monkeypatch):
    boards = {"acme": {
        "postings": [
            {"title": "Software Engineer", "externalPath": "/job/ok",
             "locationsText": "Remote", "postedOn": "Posted Today"},
            {"title": "Software Architect", "externalPath": "/job/bad",
             "locationsText": "Remote", "postedOn": "Posted Today"},
        ],
        "details": {"/job/ok": {"jobDescription": "<p>good</p>"}},
    }}
    fake = _FakeClient(boards, fail_detail={"/job/bad"})
    _patch_client(monkeypatch, fake)

    scraper = WorkdayScraper([{"tenant": "acme", "dc": "wd1", "site": "X"}],
                             roles=["Software"], relevant_tokens=["software"])
    jobs = scraper.scrape()
    assert [j.title for j in jobs] == ["Software Engineer"]  # bad detail skipped, not raised


def test_scrape_skips_failed_board_keeps_other(monkeypatch):
    boards = {
        "deadco": {"postings": [], "details": {}},
        "liveco": {
            "postings": [{"title": "Software Engineer", "externalPath": "/job/1",
                          "locationsText": "Remote", "postedOn": "Posted Today"}],
            "details": {"/job/1": {"jobDescription": "ok"}},
        },
    }
    fake = _FakeClient(boards, fail_list={"deadco"})
    _patch_client(monkeypatch, fake)

    scraper = WorkdayScraper(
        [{"tenant": "deadco", "dc": "wd1", "site": "X"},
         {"tenant": "liveco", "dc": "wd1", "site": "X"}],
        roles=["Software"], relevant_tokens=["software"])
    jobs = scraper.scrape()
    assert [j.company for j in jobs] == ["liveco"]  # one board failed, run survived


def test_scrape_empty_boards_no_network(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("must not build a client for an empty board list")
    monkeypatch.setattr(wd.httpx, "Client", _boom)
    assert WorkdayScraper([]).scrape() == []


# ── verify_workday_board + seam wiring ────────────────────────────────────────

def test_verify_workday_board_returns_total(monkeypatch):
    def _post(url, json=None, **k):
        return httpx.Response(200, json={"total": 1906, "jobPostings": [{}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(wd.httpx, "post", _post)
    assert verify_workday_board("https://bah.wd1.myworkdayjobs.com/BAH_Jobs") == 1906


def test_verify_workday_board_bad_url_no_network(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("host guard must reject before any request")
    monkeypatch.setattr(wd.httpx, "post", _boom)
    assert verify_workday_board("https://evil.com/External") is None
    assert verify_workday_board(None) is None


def test_verify_workday_board_non_200_is_none(monkeypatch):
    monkeypatch.setattr(wd.httpx, "post",
                        lambda url, **k: httpx.Response(404, request=httpx.Request("POST", url)))
    assert verify_workday_board("https://bah.wd1.myworkdayjobs.com/BAH_Jobs") is None


def test_verify_ats_board_routes_workday(monkeypatch):
    monkeypatch.setattr(wd.httpx, "post",
                        lambda url, **k: httpx.Response(200, json={"total": 42},
                                                        request=httpx.Request("POST", url)))
    assert verify_ats_board("workday", "https://x.wd1.myworkdayjobs.com/External") == 42


def test_build_ats_scrapers_workday_branch():
    cfg = {"workday": {"companies": [{"tenant": "leidos", "dc": "wd5", "site": "External"}]}}
    scrapers = build_ats_scrapers(["Software Engineer"], cfg)
    assert [type(s).__name__ for s in scrapers] == ["WorkdayScraper"]
    # disabled flag suppresses it; empty list yields nothing.
    assert build_ats_scrapers(["x"], {"workday": {"enabled": False,
                                                   "companies": [{"tenant": "a", "dc": "wd1", "site": "X"}]}}) == []
    assert build_ats_scrapers(["x"], {"workday": {"companies": []}}) == []
