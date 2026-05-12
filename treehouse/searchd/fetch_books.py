"""Fetch Project Gutenberg children's books for the Treehouse library.

Uses gutendex.com (an open mirror of the Gutenberg catalog) to discover
books and download EPUBs. Writes a manifest jsonl that phase-3's
search ingestor (ingest_books.py) will read alongside calibre's
metadata.db — manifest keeps the summary text close to the EPUB,
which calibre would otherwise drop on import.

Idempotent: a book whose EPUB is already on disk is skipped without
re-downloading or re-emitting a manifest line; the catalog itself is
re-fetched fresh each run so newly-promoted books trickle in.

Run via the ansible seed-books.yml playbook (which sets paths) or
directly:

    fetch_books.py --inbox /tmp/inbox --manifest /tmp/books.jsonl --target 200
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

GUTENDEX_API = "https://gutendex.com/books/"

# Subjects that occasionally slip past `topic=children` but aren't
# kid-appropriate. Kept narrow on purpose — broader topics like
# Slavery--Fiction or War--Fiction are kept because their
# kid-appropriateness is age-by-age, not blanket, and a heavy
# allowlist would also drop classics tagged only "Pirates--Fiction"
# instead of "Children's literature".
SUBJECT_BLOCKLIST: frozenset[str] = frozenset({
    "Erotica",
    "Sex--Fiction",
    "Suicide",
    "Mental illness--Fiction",
    "Drug abuse",
    "Drug addiction",
})


def _passes_filter(book: dict[str, Any]) -> bool:
    subjects = set(book.get("subjects") or [])
    return not (subjects & SUBJECT_BLOCKLIST)


def _get_json(url: str, attempts: int = 4, timeout: float = 60.0) -> dict[str, Any]:
    """GET a JSON URL with exponential backoff on transient errors.

    Gutendex occasionally times out under load; one failed page
    shouldn't kill a multi-hundred-book fetch."""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "treehouse-fetch-books/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            backoff = 2 ** i
            print(f"  catalog page failed (attempt {i + 1}/{attempts}): {e} — retry in {backoff}s",
                  file=sys.stderr)
            time.sleep(backoff)
    assert last_err is not None
    raise last_err


def fetch_catalog(target_count: int) -> Iterator[dict[str, Any]]:
    """Yield up to `target_count` filtered children's books, most-downloaded first."""
    yielded = 0
    url: str | None = f"{GUTENDEX_API}?topic=children&languages=en&page_size=32"
    while url and yielded < target_count:
        try:
            page = _get_json(url)
        except (urllib.error.URLError, TimeoutError) as e:
            # All retries exhausted on this page. Skip forward — we
            # don't know what the next URL is without parsing this
            # page, but we'd rather end with N-32 books than zero.
            print(f"  catalog page exhausted retries, stopping early: {e}", file=sys.stderr)
            return
        for book in page.get("results", []):
            if yielded >= target_count:
                return
            if _passes_filter(book):
                yield book
                yielded += 1
        url = page.get("next")


def download_book(book: dict[str, Any], inbox: Path) -> Path | None:
    """Save the EPUB to inbox/<id>.epub. Returns path or None if no
    EPUB format / download error / suspiciously small body."""
    epub_url = book["formats"].get("application/epub+zip")
    if not epub_url:
        return None
    out = inbox / f"{book['id']}.epub"
    if out.exists() and out.stat().st_size > 1024:
        return out
    try:
        req = urllib.request.Request(epub_url, headers={"User-Agent": "treehouse-fetch-books/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"  download failed {book['id']}: {e}", file=sys.stderr)
        return None
    if len(data) < 1024:
        print(f"  {book['id']} EPUB too small ({len(data)}b), skip", file=sys.stderr)
        return None
    out.write_bytes(data)
    return out


def manifest_entry(book: dict[str, Any], epub_path: Path) -> dict[str, Any]:
    return {
        "id": book["id"],
        "title": book["title"],
        "authors": [a["name"] for a in book.get("authors") or []],
        "summary": (book.get("summaries") or [""])[0],
        "subjects": list(book.get("subjects") or []),
        "bookshelves": list(book.get("bookshelves") or []),
        "language": (book.get("languages") or ["en"])[0],
        "download_count": book.get("download_count", 0),
        "epub_path": str(epub_path),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--inbox", required=True, type=Path)
    p.add_argument("--manifest", required=True, type=Path,
                   help="JSONL output: one book per line for phase-3 indexing")
    p.add_argument("--target", type=int, default=200,
                   help="How many books to acquire (after filter)")
    args = p.parse_args()

    args.inbox.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)

    fetched = skipped = 0
    with args.manifest.open("w") as mf:
        for book in fetch_catalog(args.target):
            path = download_book(book, args.inbox)
            if not path:
                skipped += 1
                continue
            mf.write(json.dumps(manifest_entry(book, path)) + "\n")
            fetched += 1
            print(f"  + {book['id']:5d} {book['title'][:60]}", file=sys.stderr)

    print(f"\ndone: {fetched} fetched / cached, {skipped} skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
