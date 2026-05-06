# Search

A single search bar that queries every content source on the box —
ZIMs, Kolibri channels, PeerTube, ebooks, Sugarizer activities — and
returns a unified, ranked, age-appropriate result set.

The component is `searchd`, a small FastAPI service running in the
Compose stack. The launcher's `/search` page consumes it; the AI
gateway also consumes it for RAG retrieval (see `ai.md`).

## Goals

1. **One bar, every source.** A kid types "volcanoes"; results from
   Wikipedia, Khan Academy, PeerTube, Calibre, Sugarizer all appear
   in one ranked list.
2. **Age-appropriate by default.** A 5-year-old searching "dinosaurs"
   doesn't get a graduate-level Kolibri course in their top result.
3. **Failure-tolerant.** If PeerTube is down, search still works for
   the other sources. The slowest backend doesn't gate the response.
4. **Fast on warm cache.** Sub-second for repeated queries.
5. **A natural seam for AI.** The aggregator's API is shaped so that
   the AI gateway can retrieve relevant sources for RAG without
   duplicating logic.

## Architecture

```
            ┌──────────────────────────────────────────┐
            │  Launcher /search?q=volcanoes&kid=alice  │
            └────────────────────┬─────────────────────┘
                                 │
                                 ▼
   ┌────────────────────────────────────────────────────────────┐
   │  searchd                                                   │
   │                                                            │
   │  1. resolve kid profile (age, age_band, history)           │
   │  2. expand query (typos, synonyms)                         │
   │  3. consult cache (sqlite) for (q, age_band) → results     │
   │  4. fan out (asyncio.gather, per-source timeout):          │
   │       ├─► kiwix      /search?pattern=...                   │
   │       ├─► kolibri    /api/content/contentnode?search=...   │
   │       ├─► peertube   /api/v1/search/videos?search=...      │
   │       ├─► calibre    /opds/search/{q}                      │
   │       └─► sugarizer  (static activity list, in-process)    │
   │  5. normalize each backend's result shape                  │
   │  6. dedupe across sources                                  │
   │  7. rank: native score × age-band weight × history weight  │
   │  8. group by kind (Read / Watch / Learn / Play / Books)    │
   │  9. log query for history bias and curation insight        │
   │  10. return JSON                                           │
   └────────────────────────────────────────────────────────────┘
```

## Service surface

```
GET /api/search
  ?q=<query>
  &kid_id=<id>           # optional; used for age-band and history
  &kind=<kinds>          # optional; comma-separated filter
  &limit=<n>             # default 30

Response:
{
  "query": "volcanoes",
  "took_ms": 142,
  "sources_queried": ["kiwix", "kolibri", "peertube", "calibre"],
  "sources_failed": [],
  "groups": {
    "article": [...],
    "video": [...],
    "course": [...],
    "exercise": [...],
    "book": [...],
    "activity": [...]
  },
  "total": 47
}
```

A second endpoint exists for the AI gateway:

```
POST /api/retrieve
  body: {q, kid_id, max_results, kinds?}
Response: {results: [...]}    # raw, ungrouped, snippet-rich
```

`/api/retrieve` is shaped for RAG: snippets are full-length (not
truncated for UI), each result includes the source URL the AI can
cite, and there's no UI grouping. See `ai.md`.

## Per-backend endpoints

| Source | Endpoint | Returns |
|---|---|---|
| **Kiwix** | `GET /search?books.filter.lang=eng&pattern=...&pageLength=20` (multi-book in one call) | Article hits across all loaded ZIMs with snippet + score |
| **Kolibri** | `GET /api/content/contentnode/?search=...` | Content nodes with kind (video, exercise, document), title, description, channel, thumbnail |
| **PeerTube** | `GET /api/v1/search/videos?search=...&searchTarget=local` | Videos with title, description, thumbnail, duration, uploader |
| **Calibre-web** | `/opds/search/{q}` (Atom) | Books with title, author, formats |
| **Sugarizer** | static JSON of activities, searched in-process | Activity tiles with name, description, icon |

Each fetcher lives in `searchd/sources/<name>.py` with a uniform
contract:

```python
class SearchSource(Protocol):
    name: str

    async def search(
        self,
        query: str,
        limit: int,
        timeout: float,
    ) -> list[RawResult]: ...
```

`RawResult` is the source-specific shape; normalization happens in a
separate step (see below) so adding a new source is purely
"implement `search()`, write a normalizer."

## Normalized result schema

Every result the launcher and AI gateway see is in this shape:

```python
@dataclass
class Result:
    id: str                      # globally unique, e.g. "kiwix:wikipedia_en:Volcano"
    title: str
    snippet: str                 # ~200 char excerpt
    full_text: str | None        # for /retrieve, longer excerpt for AI context
    kind: ResultKind             # article|video|course|exercise|book|activity
    source: str                  # display name: "Wikipedia", "Khan Academy", etc.
    source_id: str               # internal: "kiwix", "kolibri", ...
    thumbnail_url: str | None
    deeplink_url: str            # canonical URL the launcher renders as the link
    duration_sec: int | None
    age_band: str | None         # if the source provides one
    native_score: float          # 0..1, source's own ranking
    final_score: float           # post-rerank; populated by ranker
    metadata: dict               # source-specific extras
```

Normalization is `searchd/sources/<name>.py:normalize(raw)` — a pure
function from `RawResult` to `Result`. Easy to unit-test.

## Ranking

The aggregator's value proposition. Three factors compose:

```
final_score = native_score
            × age_band_weight(result, kid)
            × history_weight(result, kid)
            × kind_preference_weight(result, kid)
```

### `native_score`

The backend's own relevance score, normalized to 0..1. Different
backends use different scales (Kiwix BM25, Kolibri's full-text
match, PeerTube's score), so we min-max normalize per query before
combining.

### `age_band_weight`

Each result has an inferred or declared `age_band`. If the kid's
band overlaps, weight = 1.0. If the result is one band higher,
weight = 0.5. Two bands higher, 0.2. One band lower, 0.7. Two bands
lower, 0.3. The intent: bias strongly toward in-band content
without erasing serendipity.

Bands are coarse: `0-3`, `3-7`, `6-10`, `9-13`, `12-16`. Overlap
is intentional — a 6-year-old can usefully see content in either
`3-7` or `6-10`.

If a result has no declared age band, infer from source:
- Wikipedia for Schools / Wiktionary simple: `6-10`
- Khan Academy by grade level metadata
- Standard Ebooks tags
- Sugarizer activity manifest declares it

If inference fails: `null`, weight = 0.8 (slight penalty for
unknown age suitability — better than mistakenly promoting).

### `history_weight`

If this kid has previously clicked similar content, bump its score
slightly. Implementation: a per-kid TF-IDF over their click history
on the result titles + sources, multiplied by a small coefficient.
Default weight contribution capped at 1.5× to avoid filter-bubble
ossification.

This is also where "she just searched for X yesterday, today she's
searching X+Y" continuity emerges — recent queries weight related
results.

### `kind_preference_weight`

Per-kid tunable. By default, all kinds 1.0. Permissions config can
include:

```yaml
kids:
  - id: alice
    permissions:
      search_preferences:
        prefer_kinds: [video, activity]   # boost: 1.2
        avoid_kinds: []                   # demote: 0.5
```

Most useful for time-of-day shaping ("after dinner, prefer Read
over Watch") — implemented by the launcher rotating preferences in
the request based on time, rather than statically configured.

## Concurrency and timeouts

Each source has its own timeout, defaulting to **2 seconds**. The
fan-out uses `asyncio.gather(..., return_exceptions=True)` so a
slow or failing backend can't gate the others.

```python
async def search(query: str, kid: Kid) -> SearchResponse:
    sources = [s for s in self.sources if kid.allowed(s.name)]
    coros = [_with_timeout(s.search(query, limit=20), s.timeout) for s in sources]
    results = await asyncio.gather(*coros, return_exceptions=True)

    succeeded, failed = partition(results, sources)
    normalized = [n for source, raw in succeeded for n in source.normalize_all(raw)]
    deduped = dedupe_across_sources(normalized)
    ranked = rank(deduped, kid)
    grouped = group_by_kind(ranked)

    return SearchResponse(
        query=query,
        groups=grouped,
        sources_queried=[s.name for s in sources],
        sources_failed=[s.name for s in failed],
    )
```

Total response time is bounded by `max(source.timeout)` plus a small
ranking/dedupe overhead — typically well under 1 second.

## Caching

Two layers:

### Per-source response cache (memory)

`(source, query, limit)` → `RawResult[]` for 5 minutes. Saves
backend round-trips when the kid edits their query slightly or
when the AI calls `/retrieve` immediately after the launcher
called `/search`.

### Aggregated result cache (sqlite)

`(query_normalized, age_band)` → `SearchResponse` for 24 hours.
Saves the whole pipeline when popular queries repeat ("dinosaurs"
gets searched a *lot*). Keyed on age band so different kids see
appropriately ranked results from the same cache row.

Cache invalidation: bumped automatically when the updater runs
(content changed → cache stale). Manual `searchd cache clear` for
the rest.

## Dedupe across sources

The same concept often appears in multiple sources. "Volcano" is
in Wikipedia, "Volcano" Khan Academy course, "Volcanoes" PeerTube
playlist. The aggregator does not collapse these into one — they
genuinely complement each other. But it also doesn't double-count.

Dedupe rule: if two results have the same normalized title (lowercase,
strip punctuation) AND the same source_id, keep only the higher-scored.
If they have the same normalized title across different sources,
keep both (they're providing different perspectives on the topic).

Cross-source title collisions are tagged with a `companions` array
so the launcher can render them side-by-side ("Wikipedia article →
also try Khan Academy course") rather than as a wall of repetition.

## Query expansion

Two cheap improvements before the model-based AI ever enters:

### Typo correction

A small fixed dictionary of common kid misspellings:

```
"vulcanoe" → "volcano"
"dinasaur" → "dinosaur"
"egipt"    → "egypt"
```

Plus Levenshtein-distance-1 against a vocabulary built from result
titles. If the corrected query has many more results, suggest "Did
you mean: …?" in the response.

### Synonym expansion

For known concept aliases:

```yaml
# searchd/synonyms.yml
volcanoes: [volcano, eruption, lava, magma]
dinosaurs: [dinosaur, fossil, paleontology, t-rex]
space: [planets, solar system, astronomy, stars]
```

Used as an OR query when the original returns thin results.
Maintainable; not magical; doesn't need an LLM.

The AI gateway can layer richer reformulation on top — see
`ai.md` — but the aggregator works fine without it.

## Activity logging

Every query is logged to `state/searchd/history.sqlite`:

```sql
CREATE TABLE queries (
    kid_id TEXT,
    query TEXT,
    age_band TEXT,
    timestamp TIMESTAMP,
    result_count INTEGER,
    sources_failed TEXT,    -- JSON array
    PRIMARY KEY (kid_id, timestamp)
);

CREATE TABLE clicks (
    kid_id TEXT,
    result_id TEXT,
    query TEXT,             -- originating query
    timestamp TIMESTAMP
);
```

Used for:

- **History weighting** in ranking (above).
- **Parental review** ("what was Alice curious about this week?")
  rendered on `admin.kids`.
- **Curation insight** ("she keeps searching for things related to
  X; consider adding more X content to the manifest").

Privacy: stays on the box. Never leaves. Default retention 90 days,
configurable.

## RAG retrieval interface

The AI gateway calls `/api/retrieve` instead of `/api/search`:

```python
@router.post("/api/retrieve")
async def retrieve(request: RetrieveRequest) -> RetrieveResponse:
    # No grouping, no UI tweaks; longer snippets; max 8 results
    raw_results = await self.fan_out(request.q, kid=request.kid)
    deduped = dedupe_across_sources(raw_results)
    ranked = rank(deduped, kid=request.kid)[:request.max_results]
    return RetrieveResponse(results=[
        Result(
            ...,
            full_text=result.fetch_excerpt(words=400),  # bigger context
        )
        for result in ranked
    ])
```

The AI gateway formats these into the prompt context. The model never
queries backends directly; the aggregator is the only seam.

## Testing

- **Unit:** ranking weights, age-band logic, dedupe rules, query
  expansion. Pure functions, fast.
- **Per-source mocked:** stub each source's HTTP response, assert
  normalization produces the right schema.
- **Integration:** spin up `compose.test.yml` with seeded ZIM,
  Kolibri channel, sample PeerTube videos. Real fan-out, real
  ranking, asserted output.
- **Latency budget:** assert p95 of search < 1.0s and p99 < 2.0s
  against the test stack. Catches regressions early.

## Open questions

- **Should the aggregator support pagination?** Probably yes
  eventually — kids who scroll past the first 30 results need a
  way to see more. Out of scope for v1.
- **Should the AI gateway call back into searchd for follow-ups?**
  Yes — see `ai.md`. The architecture supports it; just a question
  of when to wire it.
- **Embedding-based search?** The aggregator currently relies on
  each backend's lexical search. Adding a vector store on top
  (Faiss/Chroma) over title+snippet could improve "concept" queries
  but adds significant complexity. Defer until lexical search
  proves insufficient.
