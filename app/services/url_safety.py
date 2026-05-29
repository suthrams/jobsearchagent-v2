"""SSRF defense for user-supplied URLs.

`CustomUrlScraper` fetches URLs that come straight from the user via the
Start New Run "paste URLs" field. Without boundary checks an attacker
(or a curious user) can submit:

    file:///etc/passwd             local file read
    http://localhost:6379/         probe local Redis or other internal services
    http://[::1]/internal           IPv6 loopback variant
    http://192.168.1.1/admin       LAN-scan / router probe
    http://10.x.x.x/internal       internal corporate network probe
    http://169.254.169.254/.../    cloud instance metadata (AWS/GCP/Azure)

`validate_url_for_fetch` is the boundary. It enforces:

  - scheme allowlist: only http and https. file://, ftp://, gopher://,
    javascript:, data: are all rejected.
  - host presence: empty / missing hostname is rejected.
  - resolved-IP allowlist (negative form): the hostname is DNS-resolved
    and EVERY returned address must be a routable public address. If any
    resolved address is loopback, link-local, private (RFC1918 / RFC4193),
    unspecified, multicast, or reserved, the URL is rejected.

Call this once before `httpx.get(url)` and again after each redirect (with
`follow_redirects=False` on the client so we can re-validate each hop).

Known limitations:

  - **DNS rebinding.** This validator resolves the host at validation
    time; httpx re-resolves at connect time. An attacker who controls
    the authoritative DNS for the host can return a public IP at
    validation and a private IP at fetch. A pinned-IP fetch (send
    `Host:` and connect by resolved IP) closes that. Out of scope for
    v1 — most real users of the URL field paste hosts they typed by
    hand for public job boards. Documented and left for a follow-up.
  - **IPv6 ULA / non-routable.** `ipaddress.IPv6Address.is_private`
    covers ULA (fc00::/7); we rely on that.

Calling code should catch `UnsafeURLError` distinctly from generic
fetch errors so it can record an explicit "unsafe url" reason in the
workflow errors[] log rather than burying it as a transport failure.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF safety check.

    Subclasses ValueError so callers that catch ValueError still see it,
    but is distinct enough that callers who want to record "unsafe_url"
    as an audit reason can catch it specifically.
    """


def validate_url_for_fetch(url: str) -> None:
    """Raise `UnsafeURLError` if the URL is unsafe to fetch.

    Safe = scheme in {http, https} AND host resolves to a routable public
    address. Returns None on success.
    """
    if not url:
        raise UnsafeURLError("empty URL")

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"scheme {scheme!r} not allowed (use http or https)"
        )

    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeURLError("no host in URL")

    # If the host is a literal IP, validate it directly (no DNS).
    try:
        _reject_if_blocked_ip(ipaddress.ip_address(host))
        return
    except ValueError:
        # Not an IP literal - fall through to DNS resolution.
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for {host!r}: {exc}") from exc

    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0] if sockaddr else ""
        if not ip_str or ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        _reject_if_blocked_ip(ip)


def _reject_if_blocked_ip(ip) -> None:
    # Order matters: 0.0.0.0 is both `is_unspecified` and `is_private` per
    # Python's RFC 6890 classification. Same for some link-local ranges.
    # Check the more specific labels first so the error message names the
    # actual reason a security reviewer would expect.
    if ip.is_unspecified:
        raise UnsafeURLError(f"unspecified address {ip} not allowed")
    if ip.is_loopback:
        raise UnsafeURLError(f"loopback address {ip} not allowed")
    if ip.is_link_local:
        raise UnsafeURLError(
            f"link-local address {ip} not allowed "
            "(blocks cloud instance metadata service)"
        )
    if ip.is_multicast:
        raise UnsafeURLError(f"multicast address {ip} not allowed")
    if ip.is_private:
        raise UnsafeURLError(
            f"private address {ip} not allowed (RFC 1918 / ULA)"
        )
    if ip.is_reserved:
        raise UnsafeURLError(f"reserved address {ip} not allowed")
