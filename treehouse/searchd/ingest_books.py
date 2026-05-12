"""Index the books manifest (fetch_books.py output) into MeiliSearch.

Phase-3 of the books track. Reads state/books-manifest.jsonl on the
VM and upserts one document per book into the shared Meili index so
the launcher search bar surfaces books alongside Wikipedia /
Wiktionary / Vikidia hits.

Why the manifest, not calibre's metadata.db: the Gutendex catalog
gives us a per-book summary that Gutenberg's EPUBs don't carry, and
calibre drops `comments` on import if the EPUB has none. The
manifest preserves both Gutenberg ID (for stable doc IDs) and
summary text together.

Document schema:

    id              "book_gutenberg_<id>"
    title           book title
    body            "<title> by <authors>. <summary>"  (capped at ~1000 chars)
    snippet         first ~200 chars of summary (falls back to body)
    source          "book"
    kind            "book"
    deeplink_book   ""  (unused for books — kept for kiwix schema parity)
    deeplink_path   "<title>"  (urlencoded by the search UI for
                                /search/stored/?query=… on calibre-web)
    language        "en"
    indexed_at      ISO timestamp
    category        "book"
    prominence      0-3 from gutendex download_count
    infobox_class   ""  (unused; kept for schema parity with kiwix)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import meilisearch


def prominence_from_downloads(n: int) -> int:
    """Same 0-3 scale as the Wikipedia ingestor uses, applied to
    Gutenberg's download count. 50k+ downloads → top tier (Alice,
    Pride and Prejudice scale); a few hundred → tier 0."""
    if n >= 50_000:
        return 3
    if n >= 10_000:
        return 2
    if n >= 1_000:
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--manifest", required=True, type=Path,
                   help="JSONL manifest written by fetch_books.py")
    p.add_argument("--meili-url", default="http://localhost:7700")
    p.add_argument("--meili-key", default=None)
    p.add_argument("--index", default="treehouse")
    p.add_argument("--batch-size", default=200, type=int)
    args = p.parse_args()

    if not args.manifest.exists():
        print(f"manifest not found: {args.manifest}", file=sys.stderr)
        return 1

    client = meilisearch.Client(args.meili_url, args.meili_key)

    # Idempotent: create index if missing.
    try:
        client.create_index(args.index, {"primaryKey": "id"})
    except meilisearch.errors.MeilisearchApiError as e:
        if "index_already_exists" not in str(e):
            raise

    index = client.index(args.index)
    # Keep the settings call here so book-only deployments (no kiwix
    # ingest yet) still get the same filterable schema.
    index.update_settings({
        "searchableAttributes": ["title", "body", "snippet"],
        "filterableAttributes": [
            "source", "kind", "language", "_geo", "category", "prominence"
        ],
        "sortableAttributes": ["indexed_at", "_geo", "prominence"],
    })

    indexed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    batch: list[dict] = []
    seen = 0

    def flush() -> None:
        nonlocal batch, seen
        if not batch:
            return
        index.add_documents(batch)
        seen += len(batch)
        print(f"  +{len(batch)} books (running: {seen})", file=sys.stderr)
        batch = []

    with args.manifest.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            book = json.loads(line)
            authors = ", ".join(book.get("authors") or [])
            summary = (book.get("summary") or "").strip()
            body_parts = [book["title"]]
            if authors:
                body_parts.append(f"by {authors}")
            if summary:
                body_parts.append(summary)
            body = ". ".join(body_parts)[:1000]
            snippet = (summary or body)[:200]

            batch.append({
                "id": f"book_gutenberg_{book['id']}",
                "title": book["title"],
                "body": body,
                "snippet": snippet,
                "source": "book",
                "kind": "book",
                "deeplink_book": "",
                "deeplink_path": book["title"],
                "language": book.get("language", "en"),
                "indexed_at": indexed_at,
                "category": "book",
                "prominence": prominence_from_downloads(book.get("download_count", 0)),
                "infobox_class": "",
            })
            if len(batch) >= args.batch_size:
                flush()
    flush()

    print(f"\ndone: {seen} books indexed -> '{args.index}'", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
