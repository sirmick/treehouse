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
| Library v0 | Standard Ebooks, all subjects (with kid-appropriateness filter, see below) |
| Per-kid reading progress | Anonymous v0; revisit when the identity layer (`kids.yml` → backend accounts) lands |
| Search integration | title + author + description |
| Where the seed runs | Ansible task at `make provision` time |

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

## Standard Ebooks pipeline

Standard Ebooks publishes an Atom OPDS feed at
`https://standardebooks.org/opds`. It lists every book with
title / author / `dc:description` / subjects / EPUB download URL.
About 700 titles total — polished public-domain classics with
hand-edited summaries. Every book has a proper description.

Seed playbook flow:

1. Fetch the OPDS feed (XML).
2. Filter by subject (see below).
3. For each surviving entry, download the EPUB to
   `content/books/inbox/<slug>.epub`. Skip if file already present
   (idempotency check #1).
4. Once per provision: run a one-shot `linuxserver/calibre` container
   with `calibredb add /inbox/*.epub --library-path /library --duplicates`,
   then exit. Calibre's `metadata.db` deduplicates by title+author
   (idempotency check #2 — `--duplicates` keeps Calibre from
   complaining, the DB itself rejects exact duplicates).
5. The long-lived `calibre-web` container reads `/library` and serves it.

## Kid-appropriateness filter

Standard Ebooks is curated for typography quality, not age. Famous
authors with serious adult themes are on the list: Bovary, Crime and
Punishment, Heart of Darkness, Lady Chatterley, etc. Three layers of
filtering:

1. **Subject allowlist** — only books tagged at least one of:
   - `Children's literature`
   - `Fairy tales`
   - `Adventure stories`
   - `Detective and mystery stories`
   - `Science fiction`
   - `Fantasy fiction`
   - `Animal stories`
   - `Humorous stories`

2. **Subject blocklist** — any book also carrying these gets dropped
   regardless of the allowlist:
   - `Erotica`, `Sex--Fiction`
   - `Suicide`, `Mental illness--Fiction`
   - `Drug abuse`, `Drug addiction`
   - `Slavery--Fiction` (case by case — many are educationally important)
   - `War--Fiction` (case by case)

3. **Manual `force_include` list** in `group_vars/all.yml` for known-good
   titles the subject filter misses. e.g. Treasure Island may only be
   tagged `Pirates--Fiction`, not `Children's literature`; we'd add it
   here.

Expected initial library: ~50–150 books. Big enough that no kid can
read all of it, small enough that anything they pick has been thought
about.

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
