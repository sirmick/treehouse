"""Tests against the deployed VM stack: nginx + dnsmasq + kiwix.

These run from the laptop. 10.10.10.1 lives on the VM's kids NIC,
which is libvirt-isolated and unreachable from the host — so
`bin/test-live.sh` (or `make test-live`) sets up an SSH local-forward
through the management interface and points TREEHOUSE_HOST at
127.0.0.1:18080. The HTTP requests originate inside the VM, where
10.10.10.1 is local.

DNS is bypassed by setting Host: headers explicitly — so these tests
don't depend on having configured systemd-resolved.

Run with: make test-live
"""

from __future__ import annotations

import requests


def _get(host: str, vhost: str, path: str = "/", timeout: float = 5) -> requests.Response:
    return requests.get(
        f"http://{host}{path}",
        headers={"Host": vhost},
        timeout=timeout,
        allow_redirects=False,
    )


def test_home_kids_serves_placeholder(live_base: str) -> None:
    r = _get(live_base, "home.kids")
    assert r.status_code == 200
    assert b"Treehouse" in r.content


def test_wikipedia_kids_proxies_kiwix(live_base: str) -> None:
    # /catalog/v2/entries is kiwix-serve's OPDS feed of available
    # books (always present, even with an empty library).
    r = _get(live_base, "wikipedia.kids", "/catalog/v2/entries")
    assert r.status_code == 200
    assert "xml" in r.headers.get("Content-Type", "")


def test_sinkhole_returns_friendly_page(live_base: str) -> None:
    # Any unknown hostname dnsmasq sinkholed → Caddy default vhost.
    r = _get(live_base, "youtube.com")
    assert r.status_code == 200
    assert b"That's not something we have at home" in r.content


def test_admin_path_not_exposed(live_base: str) -> None:
    # admin.kids is HTTP-basic gated in production. Should NOT serve
    # without auth. (Milestone 1 has no admin yet — this test asserts
    # a request to admin.kids does not accidentally serve the home page.)
    r = _get(live_base, "admin.kids")
    # Either 401 (basicauth) or sinkhole-style fallthrough — but never
    # the home page or a kiwix proxy.
    assert b"Wikipedia for Schools" not in r.content
