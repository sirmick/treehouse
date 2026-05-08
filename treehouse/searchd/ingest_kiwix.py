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
    index.update_settings({
        "searchableAttributes": ["title", "body", "snippet"],
        "filterableAttributes": ["source", "kind", "language"],
        "sortableAttributes": ["indexed_at"],
    })

    indexed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    batch: list[dict] = []
    articles_seen = 0
    chunks_indexed = 0

    def flush() -> None:
        nonlocal batch, chunks_indexed
        if not batch:
            return
        index.add_documents(batch)
        chunks_indexed += len(batch)
        print(f"  +{len(batch)} chunks (running: {chunks_indexed})", file=sys.stderr)
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

        for ci, chunk in enumerate(chunk_text(text)):
            base = f"kiwix:{book_name}:{path}:{ci}"
            batch.append({
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
            })
            if len(batch) >= args.batch_size:
                flush()

    flush()

    print(f"\ndone: {chunks_indexed} chunks from {articles_seen} articles -> '{args.index}'",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
