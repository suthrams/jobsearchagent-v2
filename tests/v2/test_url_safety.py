"""Tests for the SSRF defense in app/services/url_safety.py.

CustomUrlScraper fetches user-supplied URLs. Without this validator a
malicious user can pivot the backend onto:
  file:///etc/passwd                local file read
  http://localhost:6379/            local Redis / internal services
  http://[::1]/internal             IPv6 loopback variant
  http://192.168.1.1/admin          LAN-scan / router
  http://10.0.0.1/internal          internal corporate
  http://169.254.169.254/...        cloud instance metadata

Each test covers one rejection class. Tests mock DNS so they run offline
and don't depend on what `example.com` happens to resolve to today.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.url_safety import (
    ALLOWED_SCHEMES,
    UnsafeURLError,
    validate_url_for_fetch,
)


# ── Scheme allowlist ─────────────────────────────────────────────────────────

def test_https_allowed():
    assert "https" in ALLOWED_SCHEMES


def test_http_allowed():
    assert "http" in ALLOWED_SCHEMES


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/file",
    "gopher://example.com/",
    "javascript:alert(1)",
    "data:text/plain;base64,SGVsbG8=",
])
def test_rejects_non_http_schemes(url):
    with pytest.raises(UnsafeURLError, match="scheme"):
        validate_url_for_fetch(url)


def test_rejects_empty_url():
    with pytest.raises(UnsafeURLError, match="empty"):
        validate_url_for_fetch("")


def test_rejects_url_with_no_host():
    with pytest.raises(UnsafeURLError, match="no host"):
        validate_url_for_fetch("http:///path-only")


# ── Literal-IP rejections (no DNS needed) ────────────────────────────────────

def test_rejects_ipv4_loopback_literal():
    with pytest.raises(UnsafeURLError, match="loopback"):
        validate_url_for_fetch("http://127.0.0.1/")


def test_rejects_ipv6_loopback_literal():
    with pytest.raises(UnsafeURLError, match="loopback"):
        validate_url_for_fetch("http://[::1]/")


def test_rejects_aws_metadata_address_literal():
    """169.254.169.254 is the AWS / GCP / Azure instance metadata endpoint.
    Hitting it from a user-supplied URL is the canonical SSRF pivot."""
    with pytest.raises(UnsafeURLError, match="link-local"):
        validate_url_for_fetch("http://169.254.169.254/latest/meta-data/")


@pytest.mark.parametrize("addr", [
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.0.1",
    "192.168.255.255",
])
def test_rejects_rfc1918_literal(addr):
    with pytest.raises(UnsafeURLError, match="private"):
        validate_url_for_fetch(f"http://{addr}/")


def test_rejects_unspecified_ipv4_literal():
    with pytest.raises(UnsafeURLError, match="unspecified"):
        validate_url_for_fetch("http://0.0.0.0/")


def test_rejects_multicast_literal():
    with pytest.raises(UnsafeURLError, match="multicast"):
        validate_url_for_fetch("http://224.0.0.1/")


# ── Hostname rejections (DNS mocked) ─────────────────────────────────────────

def _gai(*ips):
    """Stub for socket.getaddrinfo returning (family, type, proto, canon, sockaddr)
    tuples. We only care about sockaddr[0] (the IP string)."""
    return [(2, 1, 6, "", (ip, 0)) for ip in ips]


def test_rejects_localhost_hostname():
    with patch("app.services.url_safety.socket.getaddrinfo",
               return_value=_gai("127.0.0.1")):
        with pytest.raises(UnsafeURLError, match="loopback"):
            validate_url_for_fetch("http://localhost/")


def test_rejects_hostname_that_resolves_to_private():
    """A hostname that an attacker pointed at a private IP must be blocked
    even though the hostname itself is innocuous-looking."""
    with patch("app.services.url_safety.socket.getaddrinfo",
               return_value=_gai("10.0.0.5")):
        with pytest.raises(UnsafeURLError, match="private"):
            validate_url_for_fetch("http://corp-internal.example.com/")


def test_rejects_when_any_resolved_address_is_blocked():
    """Defense in depth: if a hostname resolves to BOTH a public IP and a
    private IP (DNS multi-record), reject. Otherwise an attacker can
    bypass the check by adding a public A record alongside the private."""
    with patch("app.services.url_safety.socket.getaddrinfo",
               return_value=_gai("8.8.8.8", "10.0.0.5")):
        with pytest.raises(UnsafeURLError, match="private"):
            validate_url_for_fetch("http://mixed.example.com/")


def test_dns_resolution_failure_is_rejected():
    """If DNS fails we cannot prove the host is safe, so we reject."""
    import socket as _socket
    with patch("app.services.url_safety.socket.getaddrinfo",
               side_effect=_socket.gaierror("nodename nor servname provided")):
        with pytest.raises(UnsafeURLError, match="DNS resolution"):
            validate_url_for_fetch("http://nowhere.invalid/")


# ── Allowed (public) ─────────────────────────────────────────────────────────

def test_public_address_is_allowed():
    """A public, non-special IP must pass cleanly."""
    with patch("app.services.url_safety.socket.getaddrinfo",
               return_value=_gai("93.184.216.34")):  # example.com canonical
        validate_url_for_fetch("https://example.com/jobs/123")


def test_https_url_is_allowed_alongside_http():
    with patch("app.services.url_safety.socket.getaddrinfo",
               return_value=_gai("8.8.8.8")):
        validate_url_for_fetch("https://example.com/")
        validate_url_for_fetch("http://example.com/")
