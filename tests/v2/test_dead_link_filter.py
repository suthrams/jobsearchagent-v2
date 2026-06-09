"""Unit tests for the best-effort dead-link filter (ADR-095). No real network."""
import httpx
import pytest

import app.services.dead_link_filter as dlf


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _Client:
    """Injectable fake httpx.Client for check_link."""
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc
    def get(self, url):
        if self._exc:
            raise self._exc
        return self._resp
    def close(self):
        pass


@pytest.mark.parametrize("status", [404, 410])
def test_hard_dead_statuses(status):
    assert dlf.check_link("https://x/job", client=_Client(_Resp(status))) == "dead"


def test_closed_job_marker_on_200_is_dead():
    body = "<h1>This job is No Longer Available</h1>"
    assert dlf.check_link("https://x/job", client=_Client(_Resp(200, body))) == "dead"


def test_clean_200_is_alive():
    assert dlf.check_link("https://x/job", client=_Client(_Resp(200, "Apply now!"))) == "alive"


@pytest.mark.parametrize("status", [429, 500, 503, 302])
def test_ambiguous_statuses_are_unknown_kept(status):
    assert dlf.check_link("https://x/job", client=_Client(_Resp(status))) == "unknown"


def test_transient_errors_are_unknown_kept():
    assert dlf.check_link("https://x/job", client=_Client(exc=httpx.ConnectError("dns"))) == "unknown"
    assert dlf.check_link("https://x/job", client=_Client(exc=httpx.ReadTimeout("t"))) == "unknown"


@pytest.mark.parametrize("url", [None, "", "ftp://x", "not-a-url"])
def test_missing_or_non_http_url_is_unknown(url):
    assert dlf.check_link(url, client=_Client(_Resp(404))) == "unknown"


class _Posting:
    def __init__(self, title, url):
        self.title, self.url = title, url


def test_filter_drops_only_verifiably_dead(monkeypatch):
    posts = [_Posting("A", "https://a"), _Posting("B", "https://b"),
             _Posting("C", "https://c")]
    verdicts = {"https://a": "dead", "https://b": "alive", "https://c": "unknown"}
    monkeypatch.setattr(dlf, "check_link", lambda url, **kw: verdicts[url])

    kept, dropped, sample = dlf.filter_dead_links(posts, client=object())
    assert [p.url for p in kept] == ["https://b", "https://c"]   # alive + unknown kept
    assert dropped == 1
    assert sample == [{"title": "A", "url": "https://a"}]


def test_filter_empty_input():
    kept, dropped, sample = dlf.filter_dead_links([], client=object())
    assert kept == [] and dropped == 0 and sample == []
