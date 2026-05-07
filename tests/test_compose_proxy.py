"""Proxy host-routing tests against the compose stack.

These validate the same logic as tests/test_live.py — host-header
routing, the placeholder home page, the wikipedia.kids reverse proxy,
the sinkhole fallback — but without needing the VM. Run on every code
change as the daily-dev gate.

Technology-agnostic at the test layer: passes whether the proxy is
nginx or Caddy, as long as the routing rules in nginx/nginx.conf or
caddy/Caddyfile match the contract.
"""

from __future__ import annotations

import requests


def _get(base: str, vhost: str, path: str = "/", **kwargs) -> requests.Response:
    return requests.get(
        f"{base}{path}",
        headers={"Host": vhost},
        timeout=5,
        allow_redirects=False,
        **kwargs,
    )


def test_home_kids_serves_placeholder(proxy_base: str) -> None:
    r = _get(proxy_base, "home.kids")
    assert r.status_code == 200
    assert b"Treehouse" in r.content


def test_hello_kids_also_works(proxy_base: str) -> None:
    # Phase 0 alias for home.kids.
    r = _get(proxy_base, "hello.kids")
    assert r.status_code == 200
    assert b"Treehouse" in r.content


def test_wikipedia_kids_proxies_kiwix(proxy_base: str) -> None:
    r = _get(proxy_base, "wikipedia.kids", "/catalog/v2/entries")
    assert r.status_code == 200
    assert "xml" in r.headers.get("Content-Type", "")
    assert b"<feed" in r.content


def test_sinkhole_returns_friendly_page(proxy_base: str) -> None:
    r = _get(proxy_base, "youtube.com")
    assert r.status_code == 200
    assert b"That's not something we have at home" in r.content


def test_sinkhole_for_arbitrary_unknown(proxy_base: str) -> None:
    r = _get(proxy_base, "tiktok.com")
    assert r.status_code == 200
    assert b"That's not something we have at home" in r.content


def test_wikipedia_path_passes_through_proxy(proxy_base: str) -> None:
    """The proxy doesn't rewrite paths on wikipedia.kids — kiwix sees
    the original path."""
    r = _get(proxy_base, "wikipedia.kids", "/catalog/v2/entries")
    assert r.status_code == 200
    # Same response shape as hitting kiwix directly.
    assert b"<feed" in r.content
