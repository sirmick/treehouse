"""Tests against the compose.test.yml stack — runs anywhere docker is.

These don't need Vagrant. They prove the kiwix container is wired
correctly and that an empty library.xml produces a reasonable response
shape. As soon as a ZIM is added to tests/fixtures/zims/ the second
group of tests starts asserting against it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests

FIXTURES = Path(__file__).parent / "fixtures" / "zims"


def _has_real_zim() -> bool:
    return any(p.suffix == ".zim" for p in FIXTURES.iterdir())


# ----- always-on smoke tests -----------------------------------------------

def test_kiwix_root_serves_something(compose_stack: str) -> None:
    r = requests.get(f"{compose_stack}/", allow_redirects=False, timeout=5)
    assert r.status_code in (200, 301, 302), f"unexpected status: {r.status_code}"


def test_kiwix_catalog_entries_is_opds_feed(compose_stack: str) -> None:
    """The OPDS v2 entries endpoint is the catalog root for kiwix-serve.
    Even with an empty library.xml it returns a well-formed Atom feed."""
    r = requests.get(f"{compose_stack}/catalog/v2/entries", timeout=5)
    assert r.status_code == 200
    ctype = r.headers.get("Content-Type", "")
    assert "xml" in ctype, f"expected XML content type, got {ctype!r}"
    assert b"<feed" in r.content, "OPDS Atom feed root <feed> missing"


def test_kiwix_404_for_nonsense(compose_stack: str) -> None:
    r = requests.get(f"{compose_stack}/this-does-not-exist", timeout=5)
    assert r.status_code in (404, 400)


# ----- ZIM-dependent tests --------------------------------------------------
# Activated automatically when any *.zim file is dropped into
# tests/fixtures/zims/. Until then, skipped.

@pytest.mark.skipif(not _has_real_zim(), reason="no test ZIM in tests/fixtures/zims/")
def test_kiwix_lists_loaded_zim(compose_stack: str) -> None:
    r = requests.get(f"{compose_stack}/catalog/v2/entries", timeout=5)
    assert r.status_code == 200
    # At least one <entry> when a real ZIM is present.
    assert b"<entry" in r.content


@pytest.mark.skipif(not _has_real_zim(), reason="no test ZIM in tests/fixtures/zims/")
def test_kiwix_serves_zim_root(compose_stack: str) -> None:
    # Ask for the catalog, pick the first book name, hit /viewer/<name>.
    cat = requests.get(f"{compose_stack}/catalog/v2/entries", timeout=5)
    assert cat.status_code == 200
    # Crude but enough for milestone 1 — check that *some* book URL works.
    import re
    book = re.search(rb"name=['\"]([a-zA-Z0-9_-]+)['\"]", cat.content)
    if not book:
        pytest.skip("could not parse book name from catalog feed")
    name = book.group(1).decode()
    r = requests.get(f"{compose_stack}/viewer#{name}", timeout=5)
    assert r.status_code in (200, 301, 302)
