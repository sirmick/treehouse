# Search

A single search bar that queries every content source on the box —
ZIMs, Kolibri channels, PeerTube, ebooks, Sugarizer activities — and
returns a unified, ranked, age-appropriate result set. The same
index also feeds the AI gateway's retrieval (RAG); see `ai.md`.

The architecture is a **centralized hybrid index** (BM25 + vector)
in MeiliSearch, populated by per-source ingestors. The Treehouse
service `searchd` is a thin read shim on top — it owns kid-aware
filtering, ranking adjustments, and activity logging; it does not
own the index.

## Goals

1. **One bar, every source.** A kid types "volcanoes"; results from
   Wikipedia, Khan Academy, PeerTube, Calibre, Sugarizer all appear
   in one ranked list.
2. **Age-appropriate by default.** A 5-year-old searching "dinosaurs"
   doesn't get a graduate-level Kolibri course in their top result.
3. **Semantic, not just keyword.** Kids ask in natural language
   ("how do bees make honey?", "lava mountains"). Vector search
   finds the volcano article when "lava mountains" is the query.
4. **Failure-tolerant.** Index is populated asynchronously; a slow
   or missing source delays its content's *appearance*, not the
   user-facing search.
5. **Fast.** Sub-100 ms p95 for keyword-only, sub-300 ms for hybrid.
6. **A natural seam for AI.** The AI gateway hits the same index
   for retrieval — no parallel content extraction pipeline.

## Why centralized, not federated

An earlier draft of this doc had `searchd` querying each backend's
own search API (Kiwix's Xapian, Kolibri's REST search, PeerTube's
search) and merging results in code. That design was reconsidered
in favour of a centralized index for three reasons:

1. **The AI needs vector search anyway.** RAG retrieval is semantic,
   not keyword. To do RAG we have to extract content from each
   backend, chunk it, and embed it. Federation would only have saved
   us the keyword side; we still pay the indexing cost. Centralizing
   means we get keyword + vector from one pass.
2. **Uniform ranking.** Merging scores across "Kiwix BM25" + "Kolibri
   match score" + "PeerTube native" is fragile; weights are heuristic
   and tend to drift. With one index, everything ranks on one scale.
3. **Faceting and filtering.** "Read about volcanoes, exclude videos,
   age-band ≥ 9" is a single filter expression on a centralized
   index; it would be a per-source filter dance with federation.

What's *lost*: live data that only the backend knows — e.g., "Alice's
progress on this Kolibri lesson" — can't live in the search index.
That's fine; per-kid runtime state isn't a search-engine concern.

## Architecture

```
   ┌─────────────────────────────────────────────────────────────┐
   │  ingestors (run from the updater after content changes)      │
   │                                                              │
   │   kiwix.py      walks ZIM, chunks articles, embeds chunks    │
   │   kolibri.py    walks contentnode tree, embeds descriptions  │
   │   peertube.py   pulls video metadata + transcripts           │
   │   calibre.py    walks library, embeds book metadata + blurbs │
   │   sugarizer.py  static activity manifest, embeds names+desc  │
   │                                                              │
   │           POST /indexes/treehouse/documents (upsert)         │
   └─────────────────────────────┬────────────────────────────────┘
                                 ▼
                ┌──────────────────────────────────┐
                │  MeiliSearch                     │
                │   single index "treehouse"       │
                │   BM25 + HNSW vectors            │
                │   filterable: source, kind,      │
                │     age_band, language, ...      │
                └──────────────────────────────────┘
                                 ▲
                  POST /indexes/treehouse/search
                                 │
   ┌─────────────────────────────┴────────────────────────────────┐
   │  searchd  (thin read shim, FastAPI)                          │
   │                                                              │
   │   /api/search        kid-aware: applies age filter + boosts, │
   │                      groups by kind, logs query              │
   │   /api/retrieve      same index, vector query, longer        │
   │                      snippets, no UI grouping (for AI)       │
   │   /api/click         logs that the kid opened a result       │
   └─────────────┬───────────────────────────────┬────────────────┘
                 │                               │
                 ▼                               ▼
            launcher                       aigateway
```

The split keeps MeiliSearch generic and `searchd` opinionated. If
MeiliSearch is replaced later (say with Qdrant for disk-resident
vectors), only `searchd` changes.

## The MeiliSearch instance

Single Rust binary, deployed as a container in `compose.yml`:

```yaml
meilisearch:
  image: getmeili/meilisearch:v1.x
  restart: unless-stopped
  volumes:
    - /srv/treehouse/state/meili:/meili_data
  networks: [treehouse-net]
  environment:
    MEILI_ENV: production
    MEILI_MASTER_KEY_FILE: /run/secrets/meili_master_key
  # cgroup limits set conservatively; revisit if RSS climbs.
  mem_limit: 1g
  memswap_limit: 2g
```

State (`/srv/treehouse/state/meili/`) is in the restic backup set.
The index is rebuildable from manifest+content if lost, but
restoring from snapshot is much faster than re-embedding everything.

A single index, `treehouse`, holds documents from every source. All
filtering is by document field, not by per-source index — keeps the
query surface uniform.

## Ingestors

Each source has an ingestor module under `searchd/ingest/<source>.py`.
The contract:

```python
class Ingestor(Protocol):
    name: str

    def manifest_for_meili(self) -> IndexConfig: ...
    # Returns the MeiliSearch settings this ingestor needs:
    # filterable attributes, sortable attributes, ranking rules,
    # embedder config. Reconciled on every run.

    def walk(self, since: datetime | None) -> Iterator[Document]: ...
    # Yields documents to upsert. `since` enables incremental runs;
    # ignore it for a full rebuild.

    def removed(self, since: datetime | None) -> Iterator[str]: ...
    # IDs to delete (e.g., a ZIM was retired, a YouTube channel
    # removed from manifest, a Kolibri channel deleted).
```

The orchestrator is a small driver invoked by the updater (or by
hand via `make ai-index`):

```
updater run → content changes on disk
            → for each ingestor:
                walk(since=last_run_at)  → upsert into MeiliSearch
                removed(since=...)        → delete from MeiliSearch
            → record `last_run_at` in state/searchd/state.json
```

Walking the ZIM, the Kolibri tree, etc., is per-source code, not
abstract — but it's small (kiwix.py is ~80 lines).

### Embeddings

Two paths:

- **App-side embedding (default):** the ingestor calls a local
  embedding model (sentence-transformers `all-MiniLM-L6-v2`,
  ~22 MB, 384-dim) and POSTs documents with the vector pre-computed.
  Predictable, no extra services.
- **MeiliSearch-side embedding:** MeiliSearch ≥ 1.6 can call out to
  Ollama or OpenAI to embed documents itself. Skipped here — adds
  a network round-trip per document at index time and couples
  indexing to Ollama's availability.

Embedding model is configured globally; switching it is a re-embed
of the corpus, not a code change.

## Document schema

Every document, regardless of source, has the same shape:

```python
@dataclass
class Document:
    id: str                       # globally unique, "kiwix:wikipedia_en:Volcano#3"
    title: str
    body: str                     # full chunk text (200..1500 tokens)
    snippet: str                  # ~200-char preview for the launcher
    source: str                   # "wikipedia", "khan", "peertube", "books", ...
    kind: ResultKind              # article|video|course|exercise|book|activity
    deeplink_url: str             # canonical URL the launcher renders
    thumbnail_url: str | None
    duration_sec: int | None
    age_band: str | None          # "0-3" | "3-7" | "6-10" | "9-13" | "12-16" | null
    language: str                 # "en", "fr", ...
    embedding: list[float]        # 384-dim by default
    indexed_at: datetime
    metadata: dict                # source-specific extras
```

Configured in MeiliSearch as:

```
filterableAttributes:  source, kind, age_band, language
sortableAttributes:    indexed_at, duration_sec
searchableAttributes:  title, body, snippet     # in priority order
embedders:
  default:
    source: userProvided
    dimensions: 384
```

Adding a new source means: write an ingestor that yields documents
in this shape. The schema is the integration contract.

## Query path

### Launcher search

```
GET /api/search
  ?q=<query>
  &kid_id=<id>          # optional; used for age + history bias
  &kind=<kinds>         # optional CSV filter
  &limit=<n>            # default 30
```

Inside `searchd`:

```python
async def search(q: str, kid: Kid | None) -> SearchResponse:
    age_filter = build_age_filter(kid)            # MeiliSearch filter expr
    hybrid_ratio = 0.5                            # 0=keyword only, 1=vector only
    raw = await meili.search(
        q=q,
        filter=age_filter,
        hybrid={"semanticRatio": hybrid_ratio, "embedder": "default"},
        limit=limit * 2,                          # fetch more, rerank, trim
    )
    reranked = apply_kid_bias(raw, kid)
    grouped = group_by_kind(reranked[:limit])
    log_query(kid, q, len(raw))
    return SearchResponse(query=q, groups=grouped, took_ms=...)
```

`apply_kid_bias` is small: history-weight, kind-preference, mild
age-band drift toward in-band content (in addition to the hard
filter). MeiliSearch has done the heavy lifting; the shim is a
re-rank, not a re-search.

### AI retrieval

```
POST /api/retrieve
  body: {q, kid_id, max_results, kinds?}
```

Same index, vector-heavy hybrid (`semanticRatio: 0.85`), longer
`body` returned per result, no grouping:

```python
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    raw = await meili.search(
        q=req.q,
        filter=build_age_filter(req.kid),
        hybrid={"semanticRatio": 0.85, "embedder": "default"},
        limit=req.max_results,
        attributesToRetrieve=["id", "title", "body", "source", "deeplink_url"],
    )
    return RetrieveResponse(results=raw)
```

The AI gateway formats these into the prompt context.

## Ranking

Most ranking happens inside MeiliSearch with its default rule set
(words → typo → proximity → attribute → sort → exactness, plus the
hybrid score). `searchd` applies a small post-rerank for kid-specific
bias:

```
final_score = meili_score
            × age_band_weight(result, kid)
            × history_weight(result, kid)
            × kind_preference_weight(result, kid)
```

### `age_band_weight`

Hard filter knocks out wildly off-band content. Within the surviving
set, gentle bias toward in-band:

- exact band match: 1.0
- one band off: 0.85
- two bands off: 0.6

Bands: `0-3`, `3-7`, `6-10`, `9-13`, `12-16`. Overlap intentional —
a 6-year-old can usefully see content in either `3-7` or `6-10`.

### `history_weight`

Per-kid TF-IDF over their click history on result titles + sources,
multiplied by a small coefficient. Capped at 1.5× to avoid
filter-bubble ossification. See `state/searchd/clicks.sqlite`.

This is also where "she just searched for X yesterday, today she's
searching X+Y" continuity emerges.

### `kind_preference_weight`

Per-kid tunable in `kids.yml.permissions`:

```yaml
kids:
  - id: alice
    permissions:
      search_preferences:
        prefer_kinds: [video, activity]   # boost: 1.2
        avoid_kinds: []                   # demote: 0.5
```

Most useful for time-of-day shaping ("after dinner, prefer Read over
Watch") — implemented by the launcher rotating preferences in the
request based on time, rather than statically configured.

## Caching

Mostly free. MeiliSearch caches its own internal structures and
serves repeated queries fast (sub-10 ms when the index is hot).
`searchd` adds a small in-process LRU on the *fully-reranked*
response keyed by `(q_normalized, age_band, kid_kind_prefs_hash)`
with a 60-second TTL. Mostly to absorb React-style "search every
keystroke" load from the launcher; not a correctness mechanism.

No sqlite cache layer — that was a federation-era workaround and
isn't needed here.

## Activity logging

`state/searchd/history.sqlite`:

```sql
CREATE TABLE queries (
    kid_id TEXT,
    query TEXT,
    timestamp TIMESTAMP,
    result_count INTEGER,
    PRIMARY KEY (kid_id, timestamp)
);

CREATE TABLE clicks (
    kid_id TEXT,
    result_id TEXT,           -- MeiliSearch document ID
    query TEXT,               -- originating query
    timestamp TIMESTAMP
);
```

Used for:

- **History weighting** in the rerank.
- **Per-kid context profile** (see `ai.md` § Per-kid context profile).
- **Parental review** rendered on `admin.kids/profile/<kid_id>`.
- **Curation insight** ("she keeps searching for things related to
  X; consider adding more X content").

Privacy: stays on the box. Default retention 90 days.

## Query expansion

MeiliSearch's typo tolerance handles most kid misspellings out of
the box (Levenshtein ≤ 2 by default). Synonym lists go in the index
settings, not in app code:

```json
PUT /indexes/treehouse/settings/synonyms
{
  "volcanoes": ["volcano", "eruption", "lava", "magma"],
  "dinosaurs": ["dinosaur", "fossil", "paleontology", "t-rex"],
  "space":     ["planets", "solar system", "astronomy", "stars"]
}
```

Maintained in `searchd/synonyms.yml`, pushed to MeiliSearch on
service start. The AI gateway can layer richer reformulation on top
— see `ai.md` — but the index works fine without it.

## Failure modes

| Failure | Behaviour |
|---|---|
| MeiliSearch unreachable | `searchd` returns 503 with a friendly error; launcher renders "search is taking a break, try in a moment" |
| Embedding model unavailable at index time | Ingestor retries with backoff; if persistent, document indexed *without* a vector. Keyword-only retrievable; semantic search misses it. Logged as a degraded-state warning in the next update report. |
| Embedding model unavailable at query time | Hybrid query falls back to keyword-only (MeiliSearch handles this transparently if the embedder is `userProvided`). |
| Stale index (content updated, ingest not yet run) | Old results remain searchable; new content invisible until next ingest. Reconciled at next updater run. |

## Testing

- **Unit:** `apply_kid_bias`, age-band filter generation, document
  normalization. Pure functions, fast.
- **Per-ingestor:** stub the source, assert documents emitted match
  the schema. No MeiliSearch needed.
- **Integration:** `compose.test.yml` brings up MeiliSearch + a
  small seeded index (one ZIM, one Kolibri channel, two test
  videos). Real query path, asserted output.
- **Latency budget:** p95 search < 300 ms, p95 retrieve < 500 ms
  against the test stack. Catches regressions early.

## Open questions

- **Should the AI gateway query MeiliSearch directly?** It currently
  goes through `searchd /api/retrieve` so kid-specific filters and
  logging live in one place. Direct would shave one hop but loses
  the seam. Defer.
- **Does the launcher search bar need pagination?** Yes eventually.
  Out of scope for v1.
- **When does the vector index get big enough to matter?** Wikipedia
  alone is fine (~80 MB at 384-dim). Adding Kolibri + PeerTube +
  books pushes toward 500 MB–1 GB of HNSW in RAM. Cross that bridge
  if/when we get there; options at that point include truncating
  embedding dimensions, swapping the embedder for a smaller one, or
  moving the vector store to Qdrant (disk-resident HNSW).
- **Cross-language search?** Kids' segment is single-language for
  v1 (`language: en`). Multilingual embedding models exist
  (`paraphrase-multilingual-MiniLM-L12-v2`); swap is a config
  change. Defer until there's a multi-language manifest.
