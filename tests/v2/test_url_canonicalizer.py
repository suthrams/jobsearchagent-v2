"""Tests for app/services/url_canonicalizer.py.

The bug this module closes (2026-05-29): Adzuna's redirect URLs carry a
rotating `?se=<token>` tracking parameter, so the same ad fetched twice
appeared at two different URLs and was treated as two different jobs by
the per-URL dedup. After canonicalization, both fetches produce the same
URL and dedup catches the duplicate.
"""
from app.services.url_canonicalizer import canonicalize_url


def test_strips_adzuna_query_and_fragment():
    url = (
        "https://www.adzuna.com/land/ad/5690461826"
        "?se=eFiZbnFZ8RGcOYs2Ni-pJA"
        "&utm_medium=api"
        "&utm_source=9e84c123"
        "&v=830486E958CE7A8E46833FC7AA1761AA0DA5C17E#anchor"
    )
    assert canonicalize_url(url) == "https://www.adzuna.com/land/ad/5690461826"


def test_same_adzuna_ad_two_rotating_tokens_yields_same_canonical():
    """Load-bearing regression test for the cyber-grad 'finding the same
    job over and over' bug. Two fetches of ad 5690461826 with different
    `?se=` tokens MUST produce the same canonical URL so per-user dedup
    catches the duplicate."""
    url1 = "https://www.adzuna.com/land/ad/5690461826?se=eFiZbnFZ8RGcOYs2Ni-pJA&utm_medium=api"
    url2 = "https://www.adzuna.com/land/ad/5690461826?se=zFirR-tZ8RGs3Me5X3pAAw&utm_medium=api"
    assert canonicalize_url(url1) == canonicalize_url(url2)
    assert canonicalize_url(url1) == "https://www.adzuna.com/land/ad/5690461826"


def test_adzuna_url_with_no_query_unchanged():
    url = "https://www.adzuna.com/land/ad/5690461826"
    assert canonicalize_url(url) == url


def test_non_adzuna_urls_pass_through_unchanged():
    """LinkedIn, custom, generic URLs are not canonicalized - if those
    sources later turn out to rotate query params too, we'll add per-
    source branches. Today we only have evidence for Adzuna."""
    cases = [
        "https://www.linkedin.com/jobs/view/123456789/?trackingId=foo",
        "https://greenhouse.io/some-co/jobs/abc?gh_src=xyz",
        "https://example.com/job/1",
        "http://plain.example/path",
    ]
    for url in cases:
        assert canonicalize_url(url) == url, f"non-Adzuna URL was modified: {url}"


def test_empty_and_none_return_empty_string():
    """Matches the previous `getattr(v1_job, "url", "")` fallback - the
    caller (JobDiscoveryService.normalize) expects a string."""
    assert canonicalize_url("") == ""
    assert canonicalize_url(None) == ""


def test_malformed_input_returns_input_unchanged():
    """urlparse is very tolerant; this test mainly asserts we don't raise
    on weird inputs and that non-URL strings are returned as-is."""
    assert canonicalize_url("not a url at all") == "not a url at all"


def test_subdomain_variants_of_adzuna_are_canonicalized():
    """Adzuna runs regional subdomains. All should be canonicalized."""
    for host in ("www.adzuna.com", "www.adzuna.co.uk", "us.adzuna.com"):
        url = f"https://{host}/land/ad/123?se=token&utm=foo"
        assert canonicalize_url(url) == f"https://{host}/land/ad/123"
