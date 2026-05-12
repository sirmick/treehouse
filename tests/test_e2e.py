"""Browser-driven e2e against the deployed VM (lan mode).

Hits the public FQDNs from /treehouse.yml — same path real devices use,
including the host reverse proxy / authelia LAN carve-out / HTTPS.

Skipped automatically when:
  - pytest-playwright isn't installed (CI/laptops without the browser dep)
  - treehouse.yml is in isolated mode (not reachable from here)
  - the public FQDNs don't resolve / aren't reachable

Run with:
    .venv/bin/python -m pytest tests/test_e2e.py -v

Install browser once:
    .venv/bin/playwright install chromium
"""

from __future__ import annotations

import socket
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
import yaml

playwright_sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync_api.sync_playwright
Page = playwright_sync_api.Page

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sample_distinct_colors(png_bytes: bytes, cap: int = 30) -> int:
    """Count distinct RGB triples in a PNG, stopping once cap is reached.

    Walks IDAT chunks, decompresses, undoes PNG row filters, samples
    every Nth pixel. Stdlib-only so we don't drag Pillow into tests/.
    """
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    width = height = bpp = 0
    color_type = 0
    idat = bytearray()
    while pos < len(png_bytes):
        length = struct.unpack(">I", png_bytes[pos : pos + 4])[0]
        ctype = png_bytes[pos + 4 : pos + 8]
        data = png_bytes[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, _, color_type = struct.unpack(">IIBB", data[:10])
            # 2 = RGB (3 bpp), 6 = RGBA (4 bpp). Playwright screenshots are RGBA.
            bpp = {2: 3, 6: 4}.get(color_type, 0)
            if bpp == 0:
                raise ValueError(f"unsupported PNG color type {color_type}")
        elif ctype == b"IDAT":
            idat += data
        elif ctype == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    stride = width * bpp
    seen: set[tuple[int, int, int]] = set()
    step = max(1, (width * height) // 4000)
    pixel_idx = 0
    for y in range(height):
        row_start = y * (stride + 1) + 1  # +1 skips the filter byte
        for x in range(width):
            if pixel_idx % step == 0:
                off = row_start + x * bpp
                seen.add((raw[off], raw[off + 1], raw[off + 2]))
                if len(seen) >= cap:
                    return len(seen)
            pixel_idx += 1
    return len(seen)


def _load_config() -> dict[str, Any]:
    """Read /treehouse.yml; skip if missing or not in lan mode."""
    cfg_path = REPO_ROOT / "treehouse.yml"
    if not cfg_path.exists():
        cfg_path = REPO_ROOT / "treehouse.example.yml"
    cfg = yaml.safe_load(cfg_path.read_text())
    if cfg.get("network", {}).get("mode") != "lan":
        pytest.skip("e2e only runs in lan mode (public FQDNs reachable)")
    return cfg


def _public(cfg: dict[str, Any], hid: str) -> str:
    return cfg["hostnames"][hid]["public"]


@pytest.fixture(scope="session")
def config() -> dict[str, Any]:
    cfg = _load_config()
    # Bail early if the home FQDN doesn't resolve — saves a long Playwright
    # timeout and gives a clearer error.
    host = _public(cfg, "home")
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        pytest.skip(f"{host} doesn't resolve — provision the VM first")
    return cfg


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"chromium not installed: {e} (run: playwright install chromium)")
        try:
            yield b
        finally:
            b.close()


@pytest.fixture()
def page(browser) -> Any:
    ctx = browser.new_context()
    pg = ctx.new_page()
    # Surface page console + network failures to the test report.
    pg._tree_console: list[str] = []  # type: ignore[attr-defined]
    pg.on("console", lambda msg: pg._tree_console.append(f"[{msg.type}] {msg.text}"))
    pg.on("pageerror", lambda err: pg._tree_console.append(f"[pageerror] {err}"))
    try:
        yield pg
    finally:
        ctx.close()


# --- top bar injection ------------------------------------------------------

def test_topbar_injected_on_kiwix_pages(page: Any, config: dict[str, Any]) -> None:
    """Host nginx sub_filter rewrites kiwix HTML to load topbar.js. Verify
    the element mounts and the search form points at the search FQDN."""
    wiki = _public(config, "wikipedia")
    search_host = _public(config, "search")
    page.goto(f"https://{wiki}/", wait_until="networkidle", timeout=20_000)

    # The topbar mounts into shadow DOM. The host element has no visible
    # box of its own (the fixed bar lives inside the shadow), so wait
    # for "attached", not the default "visible".
    page.wait_for_selector("#__treehouse_topbar", state="attached", timeout=10_000)

    # Inside the shadow root, verify the form action points at search FQDN.
    form_action = page.eval_on_selector(
        "#__treehouse_topbar",
        "el => el.shadowRoot.querySelector('form.search').action",
    )
    assert search_host in form_action, f"form action {form_action!r} missing {search_host!r}"

    # Body padding was pushed down so the bar doesn't overlap content.
    pad = page.evaluate("parseFloat(getComputedStyle(document.body).paddingTop)")
    assert pad >= 56, f"body padding-top {pad} should be >= 56 to clear the bar"


def test_topbar_present_on_launcher_pages(page: Any, config: dict[str, Any]) -> None:
    """Launcher pages mount the top bar via +layout.svelte (sharing the
    same topbar.js as the sub_filter path). Catches regressions where
    the layout's onMount stops appending the script."""
    home = _public(config, "home")
    page.goto(f"https://{home}/", wait_until="networkidle", timeout=20_000)
    page.wait_for_selector("#__treehouse_topbar", state="attached", timeout=10_000)
    pad = page.evaluate("parseFloat(getComputedStyle(document.body).paddingTop)")
    assert pad >= 56, f"body padding-top {pad} should be >= 56 to clear the bar"


# --- maps ------------------------------------------------------------------

def test_maps_canvas_renders_pixels(page: Any, config: dict[str, Any]) -> None:
    """The classic 'blue rect but no map' regression: MapLibre boots but
    the canvas is invisibly tiny or empty. We assert two things:

    1. The canvas has filled its container (catches the bug where
       MapLibre measures container as 0×0 at init and stays at the
       HTML5 canvas default of 300×150 — visually a thin empty strip).
    2. The canvas has rendered distinct pixel colors (catches the case
       where the canvas is sized correctly but the GL pipeline fails).
    """
    maps = _public(config, "maps")
    page.goto(f"https://{maps}/maps", wait_until="networkidle", timeout=30_000)
    page.wait_for_selector("canvas.maplibregl-canvas", state="attached", timeout=15_000)
    page.wait_for_timeout(2000)

    layout = page.evaluate(
        """() => {
            const c = document.querySelector('canvas.maplibregl-canvas');
            return c ? { w: c.width, h: c.height } : null;
        }"""
    )
    assert layout, "no map canvas in DOM"
    assert layout["h"] >= 400, (
        f"map canvas only {layout['w']}×{layout['h']} px — container probably "
        "had no height at init (canvas defaulted to ~300×150). The map is "
        "rendering, just into a sliver you can't see."
    )

    # Reading via gl.readPixels is unreliable: MapLibre defaults to
    # preserveDrawingBuffer:false, so the buffer can be empty between
    # frames. Use Playwright's element screenshot — it captures the
    # composited frame and shows the same pixels a human would see.
    png = page.locator("canvas.maplibregl-canvas").screenshot()
    distinct = _sample_distinct_colors(png)
    console_dump = "\n".join(page._tree_console[-20:])  # type: ignore[attr-defined]
    assert distinct > 5, (
        f"map screenshot has only {distinct} distinct colors — looks blank.\n"
        f"Recent console:\n{console_dump}"
    )


# --- map pins (geocoded article layer) -------------------------------------

def test_map_pins_query_returns_geo_hits(page: Any, config: dict[str, Any]) -> None:
    """On load, /maps fires a Meili _geoBoundingBox query for the world
    view and renders the results as a pin layer. We don't try to
    inspect MapLibre's GL canvas for circles (no DOM); instead we
    intercept the /api/search response and verify it contains geo'd
    hits and that each hit carries a `category` (settlement/landform/
    etc.) — the latter would silently break if the Meili filter
    `category IN [...]` regressed."""
    maps = _public(config, "maps")

    samples: list[dict[str, Any]] = []

    def grab(response: Any) -> None:
        if "/api/search" not in response.url:
            return
        try:
            body = response.json()
        except Exception:
            return
        hits = body.get("hits") or []
        samples.append({"url": response.url, "hits": hits})

    page.on("response", grab)
    page.goto(f"https://{maps}/maps", wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(2500)

    # At least one /api/search response captured, and at least one hit
    # carried both _geo and category.
    geo_hits = [h for s in samples for h in s["hits"] if h.get("_geo")]
    assert geo_hits, (
        "no geo'd hits returned from /api/search on /maps. Either the "
        "Wikipedia ingest hasn't tagged any chunk-0 docs with _geo yet, "
        "or the map page isn't issuing the bounding-box query."
    )
    cats = {h.get("category") for h in geo_hits}
    cats.discard(None)
    assert cats, (
        "/api/search returned geo'd hits with no `category` field. "
        "Either ingest needs to be re-run with the typology extractor, "
        "or the map's `category IN [...]` filter is excluding everything."
    )


# --- search ----------------------------------------------------------------

def test_search_returns_hits(page: Any, config: dict[str, Any]) -> None:
    """End-to-end: /search?q=… submits, MeiliSearch responds, results
    list renders. Catches breakage anywhere in the chain (launcher,
    /api/search proxy, MeiliSearch container)."""
    search = _public(config, "search")
    page.goto(f"https://{search}/search?q=banana", wait_until="networkidle", timeout=20_000)
    # Either a hit list or the "nothing here" message renders; both
    # require the page to have run the query. We want at least one hit
    # for a generic term against the Wikipedia ZIM.
    page.wait_for_function(
        "() => document.querySelectorAll('ul li a').length > 0 "
        "    || document.body.textContent.includes('Hmm')",
        timeout=15_000,
    )
    hit_count = page.locator("ul li a").count()
    assert hit_count > 0, "expected at least one search hit for 'banana'"


def test_search_returns_book_hits(page: Any, config: dict[str, Any]) -> None:
    """Books (Gutenberg via Gutendex, indexed by ingest_books.py) surface
    in /search alongside Wikipedia. The deeplink for a book points at
    calibre-web's search-results page (no Gutenberg→calibre ID mapping
    in the ingest path), so we just verify a hit comes back with
    source=book and the link points at the books FQDN."""
    search = _public(config, "search")
    books_host = _public(config, "books")
    page.goto(f"https://{search}/search?q=alice", wait_until="networkidle", timeout=20_000)
    page.wait_for_function(
        "() => document.querySelectorAll('ul li a').length > 0",
        timeout=15_000,
    )
    # Find the first hit linked to the books FQDN.
    book_link = page.locator(f"ul li a[href*='{books_host}']").first
    href = book_link.get_attribute("href")
    assert href and "/search/stored/?query=" in href, (
        f"expected a book hit linking to calibre-web search; got href={href!r}. "
        "Either ingest_books.py hasn't run, or urlFor() in /search regressed."
    )
