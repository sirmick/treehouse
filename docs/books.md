# Books on Treehouse

Add a third content kind — ebooks — alongside the existing Kiwix
(Wikipedia / Wiktionary / Vikidia) and the OSM map. Status: planned,
not yet built. This doc captures the decided shape so the next
session can pick up cold.

## Why books

Wikipedia is reference; Kolibri (not yet built) is lessons; the AI
gateway (not yet built) is dialogue. None of them is long-form
sustained reading — the thing kids ought to do most. A locally-served
library of polished classics fills that gap and doesn't compete with
anything else on the box.

## Choices, decided

| Question | Decision |
|---|---|
| Container | `lscr.io/linuxserver/calibre-web`, host-loopback port `:8083` |
| Library v0 | Project Gutenberg children's books, via [gutendex.com](https://gutendex.com) (open Gutenberg catalog mirror with summaries). Top ~200 by download count. |
| Per-kid reading progress | Anonymous v0; revisit when the identity layer (`kids.yml` → backend accounts) lands |
| Search integration | title + author + summary (from gutendex) + subjects |
| Where the seed runs | Ansible task triggered via `make seed-books` (and from `make provision` as a non-blocking play) |

> **Source pivot, 2026-05-12:** original plan was Standard Ebooks but
> their OPDS feed is gated behind a Patrons Circle membership.
> Project Gutenberg, accessed through the open Gutendex API, gives
> us 7,611 English children's books with summaries already attached.
> Standard Ebooks may come back later as a "polish layer" once paid
> access lands.

## Architecture fit

```
                      ┌──────────────────────────────────┐
                      │  Host nginx (proxy role)         │
                      │    books-kids.<public-domain> ── │── proxy_pass ──┐
                      └──────────────────────────────────┘                 │
                                                                            ▼
            ┌────────────────────────────────────────────────────────────────────┐
            │  VM                                                                │
            │    ┌────────────────────────┐    ┌────────────────────────────┐    │
            │    │  calibre-web container │    │  one-shot calibre sidecar  │    │
            │    │  reads + serves        │    │  imports EPUBs via         │    │
            │    │  /library              │◀───│  `calibredb add` (provision│    │
            │    └────────────────────────┘    │  time only, exits)         │    │
            │                ▲                  └────────────────────────────┘    │
            │                │                                                    │
            │     content/books/inbox/   (downloaded EPUBs, ephemeral)            │
            │     content/books/library/ (calibre-managed, metadata.db here)      │
            │     state/calibre/config/  (calibre-web's own settings DB)          │
            └────────────────────────────────────────────────────────────────────┘
                                          ▲
                                          │
                ┌─────────────────────────┴────────────────────────────┐
                │  treehouse/searchd/ingest_books.py                  │
                │  reads metadata.db → upserts into the shared Meili  │
                │  index with source=book, kind=book                  │
                └─────────────────────────────────────────────────────┘
```

Same pattern as the existing kiwix vhost: backend on loopback, host
nginx forwards by Host header, sub_filter injects the shared top bar
into HTML responses (so 📚 Books gets the same launcher-search bar that
Wikipedia pages already do).

## Gutendex pipeline

[Gutendex](https://gutendex.com) is an open JSON mirror of Project
Gutenberg's catalog. It exposes every book with title / authors /
**summaries** (auto-generated from the text, but coherent and
publishable) / subjects / bookshelves / direct EPUB download URL.
The query `?topic=children&languages=en` returns 7,611 books — far
more than we want; we take the top ~200 by `download_count` (the
default sort), which gives Alice, Wizard of Oz, Tom Sawyer, Anne of
Green Gables, Black Beauty, etc.

Seed playbook flow:

1. Fetch `https://gutendex.com/books/?topic=children&languages=en` page
   by page, stopping at the target count.
2. Apply the kid-appropriateness filter (see below) — drops books
   that slipped through `topic=children` but carry blocklist subjects.
3. For each surviving book, download the EPUB from
   `formats["application/epub+zip"]` into
   `content/books/inbox/<id>.epub`. Skip if present (idempotency #1).
4. Run `calibredb add --library-path .../library --recurse .../inbox`
   on the VM. Calibre dedupes by title+author against `metadata.db`
   (idempotency #2).
5. Write a manifest at `state/books-manifest.jsonl` — one line per
   book with `id`, `title`, `authors`, `summary`, `subjects`. Phase 3's
   search ingestor reads this (it's faster than re-parsing the
   calibre db, and keeps summary text close to the EPUB).
6. The long-lived `calibre-web` container reads `/library` and serves it.

**Why calibre via apt, not the container.** `lscr.io/linuxserver/calibre`
is an X11-desktop image that runs a browser-accessible Calibre GUI —
awkward to use as a headless sidecar. We install `calibre`
(`--no-install-recommends`, ~250 MB) on the VM and invoke `calibredb`
directly. The calibre-web container only needs the library
directory, not the Calibre tooling.

## Kid-appropriateness filter

Gutendex's `topic=children` query already restricts to books tagged
in Gutenberg's children's bookshelves. We add a tiny blocklist for
subjects that occasionally slip through:

- `Erotica`, `Sex--Fiction`
- `Suicide`, `Mental illness--Fiction`
- `Drug abuse`, `Drug addiction`

Topics like `Slavery--Fiction` or `War--Fiction` are kept — many are
educationally important and the kid-appropriateness of those titles
is age-by-age, not blanket. Easier to tune the blocklist as we find
gaps than to apply a heavy allowlist that omits Treasure Island
because it's tagged `Pirates--Fiction` instead of
`Children's literature`.

Expected initial library: ~180–200 books (target 200, a few drop to
filter / missing EPUB). Big enough that no kid can read all of it,
small enough that anything they pick has been thought about.

## Search ingestor

`treehouse/searchd/ingest_books.py`:

- Opens `content/books/library/metadata.db` (SQLite).
- Reads the `books` table — every column we want is right there:
  `id`, `title`, `author_sort`, `comments` (= description), `pubdate`,
  `series`, `series_index`, `path` (= per-book folder).
- Joins `books_tags_link` + `tags` for subjects.
- For each book emits one Meili doc:
  ```json
  {
    "id": "calibre:<book_id>",
    "title": "Treasure Island",
    "body": "<title> by <authors>. <description>",
    "snippet": "<first 200 chars of description>",
    "source": "book",
    "kind": "book",
    "deeplink_book": "<book_id>",
    "deeplink_path": "<book_id>/read/epub",
    "language": "en",
    "indexed_at": "..."
  }
  ```
- No geo, no chunking — books are short metadata records.
- Idempotent: same primary key, upserts.

`/search` already shows source-tagged hits; just extend `SOURCE_META`
in `launcher/src/routes/search/+page.svelte` and the topbar's
QUICKLINKS to include `book` (with a 📚 badge and the `books` host).

## Sequencing (three commits, in order)

1. **Phase 1 — infra plumbing.** Empty calibre-web UI reachable at the
   FQDN, top bar injected. No books yet.
   - `compose.yml`: add `calibre-web` service.
   - `treehouse.yml` + `treehouse.example.yml`: new `books` hostname.
   - `ansible/roles/proxy/templates/treehouse.conf.j2`: new
     `serves == 'calibre'` branch (proxy + sub_filter, same shape as
     `serves == 'kiwix'`).
   - `launcher/static/topbar.js`: add 📚 Books quicklink.
   - `launcher/src/routes/+page.svelte`: replace one of the "soon"
     tiles with a working Books tile.

2. **Phase 2 — seed library.**
   - `ansible/seed-books.yml` playbook with the OPDS fetch +
     allowlist/blocklist filter + `calibredb add` import.
   - New `Makefile` target `make seed-books`.
   - Add to `site.yml` as a separate play that runs at provision time
     after the main treehouse-services play. Non-blocking on failure
     so a transient network hiccup doesn't break `make provision`.

3. **Phase 3 — search.**
   - `treehouse/searchd/ingest_books.py`.
   - Extend `ansible/ingest.yml` to also run the book ingestor.
   - `launcher/src/routes/search/+page.svelte`: `SOURCE_META.book = …`.
   - E2E test: hit `https://search-kids/search?q=alice` and assert at
     least one hit has `source: book`.

## Out of scope for v0

- Per-kid reading progress (logins, bookmarks). Lands when the
  identity layer does.
- Cover images on search results. Calibre-web exposes `/cover/<id>`;
  trivial to add later.
- Full-text EPUB search. Title + author + description is enough for
  v0 — full text doubles index size and rarely changes which book a
  kid clicks on.
- Gutenberg / Open Library sources. v0.5 if 700 titles isn't enough;
  see "Scale beyond v0" below.

## Scale beyond v0

If 700 Standard Ebooks isn't enough:

- **Add Project Gutenberg** (~70k titles). Gutenberg EPUBs ship
  without descriptions, so we'd need a second-pass enricher:
  - Cross-reference each Gutenberg title against the Wikipedia ZIM
    already on the box. Many famous books have a Wikipedia article;
    its first paragraph is a usable description.
  - Fall back to Open Library API for anything still missing
    (online-only-once during seed).

This is structural, not architectural — same calibre-web reader,
same Meili index, same UI. Slot it in when the v0 library starts
feeling thin.
