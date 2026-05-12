"""Walk a Kiwix ZIM and index its articles into MeiliSearch.

Phase-0 ingestor (BM25 only). Embeddings will be added when the AI
gateway lands; for now MeiliSearch is configured for keyword search
without an embedder.

Document schema (matches docs/search.md, minus the embedding field):

    id                sanitized "kiwix:<book>:<path>:<chunk>"
    title             entry.title
    body              chunk text (paragraph-aware, ≤ ~1000 chars)
    snippet           first ~200 chars of body, for the launcher card
    source            "wikipedia" (configurable)
    kind              "article"
    deeplink_book     ZIM book name (so the URL can adapt to mode)
    deeplink_path     entry path within the book, e.g. "A/Volcano"
    language          "en"
    indexed_at        ISO timestamp at ingest time
    _geo              (optional) {lat, lng} — only on chunk 0 of articles
                      whose HTML carries coords. Drives the map's pin
                      layer via Meili's _geoBoundingBox filter.
    category          one of {settlement, country, landform, water,
                      structure, event, person, other} — coarse bucket
                      for map colouring + filter chips.
    prominence        0-3 article-byte-size tier; 3 = "household name".
                      Sortable so the map can show the most-notable 100
                      pins in a viewport instead of random ones.
    infobox_class     raw MediaWiki infobox classname (e.g. "infobox
                      ib-settlement vcard") — kept for future
                      finer-grained slicing without re-ingest.

Wikipedia ZIMs render coords via three redundant markers; Vikidia ZIMs
ship no coords at all. The extractor takes the first marker it finds:
1. <span class="geo">lat; lon</span>       (machine-readable Geo µformat)
2. <meta name="geo.position" content="lat;lon">
3. <meta name="ICBM" content="lat, lon">

Run via `make ingest`; the Makefile target downloads the ZIM if
missing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import meilisearch
from libzim.reader import Archive


class _TextExtractor(HTMLParser):
    """Strip HTML tags, keep text. Insert paragraph breaks on block-close."""

    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "tr", "section"}
    SKIP = {"script", "style", "nav", "header", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self._buf: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK:
            self._buf.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._buf.append(data)

    def text(self) -> str:
        raw = "".join(self._buf)
        # Collapse whitespace runs but preserve paragraph breaks.
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def chunk_text(text: str, max_chars: int = 1000) -> list[str]:
    """Split text into paragraph-aware chunks of ≤ max_chars.

    Greedy: accumulates paragraphs until adding the next would exceed
    the cap, then starts a new chunk. Paragraphs longer than max_chars
    are kept whole (chunking inside a paragraph would cut sentences)."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if current and current_len + len(para) > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def safe_id(s: str) -> str:
    """MeiliSearch document IDs allow [a-zA-Z0-9_-] only; sanitize the rest."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)[:480]


# Three independent coord patterns. Listed in order of reliability:
# Geo µformat is the cleanest (purpose-built, no DMS conversion); meta
# tags are fallbacks for the rare article that omits the span.
_GEO_PATTERNS = (
    re.compile(r'<span[^>]*class="geo"[^>]*>\s*(-?\d+(?:\.\d+)?)\s*[;,]\s*(-?\d+(?:\.\d+)?)\s*</span>'),
    re.compile(r'<meta\s+name="geo\.position"\s+content="\s*(-?\d+(?:\.\d+)?)\s*[;,]\s*(-?\d+(?:\.\d+)?)\s*"'),
    re.compile(r'<meta\s+name="ICBM"\s+content="\s*(-?\d+(?:\.\d+)?)\s*[;,]\s*(-?\d+(?:\.\d+)?)\s*"'),
)


def extract_geo(html: str) -> tuple[float, float] | None:
    """Return (lat, lng) if the article HTML carries coords, else None.

    Sanity-clamps to valid earth-coord ranges so a junk match in a
    template string can't poison the index with lat=500."""
    for pat in _GEO_PATTERNS:
        m = pat.search(html)
        if not m:
            continue
        try:
            lat, lng = float(m.group(1)), float(m.group(2))
        except ValueError:
            continue
        if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
            return lat, lng
    return None


_INFOBOX_RE = re.compile(r'<table[^>]*class="([^"]*infobox[^"]*)"')
_FIRSTP_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_PAREN_RE = re.compile(r"\([^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")

# Keyword → category. The match pattern below anchors on "is/was a"
# (etc.) so we classify articles by what they ARE, not what they
# mention. Without the anchor an article on the 37th parallel north
# becomes "water" because its first paragraph names the Atlantic.
_KEYWORD_TO_CATEGORY: dict[str, str] = {}
for _kw in (
    "river stream creek lake sea ocean bay gulf strait fjord reservoir "
    "waterfall delta estuary lagoon"
).split():
    _KEYWORD_TO_CATEGORY[_kw] = "water"
for _kw in (
    "mountain peak hill volcano stratovolcano valley plateau cliff cape "
    "glacier desert island archipelago peninsula cave forest canyon reef"
).split():
    _KEYWORD_TO_CATEGORY[_kw] = "landform"
for _kw in (
    "bridge tower palace castle monument landmark museum cathedral "
    "temple church mosque stadium airport skyscraper library university "
    "fortress"
).split():
    _KEYWORD_TO_CATEGORY[_kw] = "structure"
for _kw in (
    "battle war treaty festival ceremony massacre expedition earthquake "
    "eruption disaster uprising revolution"
).split():
    _KEYWORD_TO_CATEGORY[_kw] = "event"
for _kw in "city town village hamlet municipality capital".split():
    _KEYWORD_TO_CATEGORY[_kw] = "settlement"
for _kw in "country nation kingdom state province".split():
    _KEYWORD_TO_CATEGORY[_kw] = "country"

# "X is a Y" / "X was the Y" / "X are one of the most famous Ys" —
# anchor on a copula + determiner so we classify by article subject,
# not by any noun mentioned in the lede. The keyword is captured in
# group 1; trailing 's' is optional so plurals match.
_LEDE_RE = re.compile(
    r"\b(?:is|was|are|were)\s+(?:a|an|the|one of(?:\s+the)?)\s+"
    r"(?:\S+\s+){0,6}?(" + "|".join(sorted(_KEYWORD_TO_CATEGORY, key=len, reverse=True)) + r")s?\b",
    re.I,
)


def extract_first_paragraph(html: str) -> str:
    """Plain-text first <p> from a Wikipedia article (parens stripped)."""
    m = _FIRSTP_RE.search(html)
    if not m:
        return ""
    text = _TAG_RE.sub("", m.group(1))
    text = _PAREN_RE.sub("", text)
    return text.lower().strip()


def extract_infobox_class(html: str) -> str:
    m = _INFOBOX_RE.search(html)
    return m.group(1).strip() if m else ""


def classify(infobox_class: str, first_paragraph: str) -> str:
    """One of: settlement | country | landform | water | structure |
    event | person | other.

    Infobox classes are the most reliable signal when they're specific
    (`ib-settlement`, `ib-country`, etc.). Plain `vcard` is a
    catch-all microformat used on everything from mountains to
    landmarks, so we fall through to first-paragraph keyword matching
    anchored on "X is a Y" for the catch-all case."""
    ic = infobox_class.lower()
    if "ib-settlement" in ic:
        return "settlement"
    if "ib-country" in ic:
        return "country"
    if "vevent" in ic:
        return "event"
    if "biography" in ic:
        return "person"

    m = _LEDE_RE.search(first_paragraph)
    if m:
        return _KEYWORD_TO_CATEGORY[m.group(1).lower()]
    return "other"


def prominence_tier(byte_size: int) -> int:
    """0..3 from article HTML size. Used as the "show me the
    big-deal stuff first" sort key for the map."""
    if byte_size >= 100_000:
        return 3
    if byte_size >= 40_000:
        return 2
    if byte_size >= 10_000:
        return 1
    return 0


def get_book_name(archive: Archive, fallback: str) -> str:
    """Pull the ZIM's Name metadata, fall back to the file stem."""
    try:
        raw = archive.get_metadata("Name")
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)
    except Exception:
        return fallback


def iter_html_entries(archive: Archive):
    """Yield (path, title, html) for every non-redirect HTML entry in the ZIM."""
    for i in range(archive.entry_count):
        try:
            entry = archive._get_entry_by_id(i)
        except Exception:
            continue
        if entry.is_redirect:
            continue
        try:
            item = entry.get_item()
        except Exception:
            continue
        mime = item.mimetype or ""
        if "html" not in mime:
            continue
        try:
            html = bytes(item.content).decode("utf-8", errors="replace")
        except Exception:
            continue
        yield entry.path, entry.title, html


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--zim", required=True, type=Path,
                   help="Path to the .zim file to ingest")
    p.add_argument("--meili-url", default="http://localhost:7700",
                   help="MeiliSearch URL")
    p.add_argument("--meili-key", default=None,
                   help="MeiliSearch master/admin key (omit in dev mode)")
    p.add_argument("--index", default="treehouse",
                   help="MeiliSearch index name")
    p.add_argument("--source", default="wikipedia",
                   help="`source` value for indexed documents")
    p.add_argument("--kind", default="article",
                   help="`kind` value for indexed documents (article/definition/etc.)")
    p.add_argument("--book-name", default=None,
                   help="Override the ZIM's Name metadata; defaults to its self-declared name")
    p.add_argument("--batch-size", default=200, type=int)
    p.add_argument("--limit", default=None, type=int,
                   help="Stop after N articles (smoke testing)")
    args = p.parse_args()

    if not args.zim.exists():
        print(f"ZIM not found: {args.zim}", file=sys.stderr)
        return 1

    archive = Archive(str(args.zim))
    # Two distinct book identifiers:
    #   book_name — ZIM's internal Name metadata (e.g. "wikipedia_en_simple_all").
    #               Used for stable document IDs across re-ingest of newer
    #               versions of the same ZIM (the metadata stays stable; the
    #               filename's version suffix doesn't).
    #   book_id   — kiwix-serve's per-book URL segment, which is the ZIM
    #               filename without extension (e.g.
    #               "wikipedia_en_simple_all_maxi_2026-02"). Used in
    #               deeplink_book so search-result URLs route correctly.
    book_name = args.book_name or get_book_name(archive, args.zim.stem)
    book_id = args.zim.stem
    print(f"opening {args.zim} (name={book_name}, kiwix-id={book_id}, entries={archive.entry_count})",
          file=sys.stderr)

    client = meilisearch.Client(args.meili_url, args.meili_key)

    # Idempotent: create index if missing, otherwise leave existing in place.
    # We don't wait on these tasks — MeiliSearch processes them in order
    # behind any ongoing indexing work, and a busy queue can blow past
    # the python client's default 5s wait. add_documents below queues
    # after them by virtue of order.
    try:
        client.create_index(args.index, {"primaryKey": "id"})
    except meilisearch.errors.MeilisearchApiError as e:
        if "index_already_exists" not in str(e):
            raise

    index = client.index(args.index)
    # `_geo` is a Meili-special field for geosearch. Adding it to both
    # filterable and sortable enables `_geoBoundingBox(...)` filters
    # (used by the map's pin layer) and `_geoPoint(...)` sort.
    # `category` + `prominence` drive the map's per-type layers and
    # "show me the big-deal stuff first" sort.
    index.update_settings({
        "searchableAttributes": ["title", "body", "snippet"],
        "filterableAttributes": [
            "source", "kind", "language", "_geo", "category", "prominence"
        ],
        "sortableAttributes": ["indexed_at", "_geo", "prominence"],
    })

    indexed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    batch: list[dict] = []
    articles_seen = 0
    chunks_indexed = 0
    geo_articles = 0

    def flush() -> None:
        nonlocal batch, chunks_indexed
        if not batch:
            return
        index.add_documents(batch)
        chunks_indexed += len(batch)
        print(f"  +{len(batch)} chunks (running: {chunks_indexed}, geo: {geo_articles})",
              file=sys.stderr)
        batch = []

    for path, title, html in iter_html_entries(archive):
        if args.limit and articles_seen >= args.limit:
            break
        articles_seen += 1

        ext = _TextExtractor()
        try:
            ext.feed(html)
        except Exception:
            continue
        text = ext.text()
        if len(text) < 200:
            continue

        # Extract typology + geo from the raw HTML (not the stripped
        # text — the extractor drops the marker spans). Geo lands on
        # chunk 0 only so one article = at most one map pin; category
        # and prominence are duplicated to every chunk so future
        # search-side filtering by them works regardless of which
        # chunk matches.
        geo = extract_geo(html)
        infobox_class = extract_infobox_class(html)
        first_p = extract_first_paragraph(html)
        category = classify(infobox_class, first_p)
        prominence = prominence_tier(len(html))
        if geo is not None:
            geo_articles += 1

        for ci, chunk in enumerate(chunk_text(text)):
            base = f"kiwix:{book_name}:{path}:{ci}"
            doc: dict = {
                "id": safe_id(base),
                "title": title,
                "body": chunk,
                "snippet": chunk[:200],
                "source": args.source,
                "kind": args.kind,
                "deeplink_book": book_id,
                "deeplink_path": path,
                "language": "en",
                "indexed_at": indexed_at,
                "category": category,
                "prominence": prominence,
                "infobox_class": infobox_class,
            }
            if ci == 0 and geo is not None:
                doc["_geo"] = {"lat": geo[0], "lng": geo[1]}
            batch.append(doc)
            if len(batch) >= args.batch_size:
                flush()

    flush()

    print(f"\ndone: {chunks_indexed} chunks from {articles_seen} articles "
          f"({geo_articles} with geo) -> '{args.index}'",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
