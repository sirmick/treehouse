# Content

What lives on the box, where it comes from, how it's kept fresh, and how
the operator (Mick) tells the system what to fetch.

The whole content layer is driven by one declarative file —
`manifest.yml` — and one idempotent script — `updater`. Editing the
file and running the script is the entire user-facing API for content
management.

## Principles

1. **Manifest is truth.** The manifest names every piece of content
   that should exist on the box. Anything on disk that isn't in the
   manifest is either pending download or eligible for prune. Anything
   in the manifest that isn't on disk gets fetched on the next run.
2. **Idempotent by default.** Running the updater on a clean box and
   running it on a fully-populated box do the same logical work; the
   second one just discovers it has nothing to do.
3. **Native "what's new" mechanisms.** Each source has its own way of
   indicating freshness (Kiwix OPDS catalog, yt-dlp's download archive,
   Kolibri's importchannel, HTTP `Last-Modified`). The updater uses
   each source's native mechanism rather than reinventing.
4. **Plan before apply.** Every run produces a plan first. `--dry-run`
   prints it without acting. The apply phase walks the plan;
   per-item failures don't kill the run.
5. **Reports beat logs.** Each run writes a markdown report at
   `logs/update-YYYY-MM-DD.md`. The report is the artifact you read on
   a Sunday morning to know whether the network is healthy. If the
   report is unreadable, the updater has failed in the most important
   way.

## `manifest.yml` — the user-facing surface

A single YAML file at the repo root. Mick edits it; the updater
reconciles to it.

> **Status (M1):** the file exists at `manifest.yml` and is
> schema-validated by `make test-schemas`. The Phase-3 updater isn't
> built yet; today the only consumer is `make seed-wikipedia`, which
> reads `zims[0].name` and pairs it with a hardcoded `WIKIPEDIA_SEED_DATE`
> in the Makefile to fetch a single ZIM. Adding more entries here
> right now means they don't get fetched yet — but the file is
> already in the right shape for when the updater lands.

```yaml
zims:
  # source: library.kiwix.org. Always pulls newest published version.
  - name: wikipedia_en_all_nopic
    keep: latest                  # or keep: 2 to retain previous
  - name: wiktionary_en_all
  - name: gutenberg_en_all
  - name: stackoverflow_en_all
  - name: ted_en_all
  - name: wikivoyage_en_all
  - name: wikibooks_en_all_nopic

youtube:
  - channel: https://www.youtube.com/@3blue1brown
  - channel: https://www.youtube.com/@veritasium
  - channel: https://www.youtube.com/@SmarterEveryDay
  - channel: https://www.youtube.com/@kurzgesagt
    since: 2020-01-01             # date filter
    max_videos: 100               # cap on initial backfill
  - playlist: https://www.youtube.com/playlist?list=PLxxxx
    label: "Bedtime stories"

kolibri:
  - token: 95a52b386f2c485cb97dd60901674a98
    name: "Khan Academy English"
  - token: a25c5d7e0f3a4b9e8d2c1b6a7f4e3d2c
    name: "CK-12"

osm:
  - region: europe/great-britain
    provider: geofabrik
  - region: north-america/canada
    provider: geofabrik

calibre:
  - feed: https://standardebooks.org/feeds/atom/all
    filter:
      tags_any: [children, "young-adult", fairy-tales]
  # Direct additions allowed too:
  - url: https://standardebooks.org/ebooks/.../something.epub
    title: "..."

policies:
  bandwidth_limit: 10M            # bytes/sec, shared across downloaders
  disk_reserve: 50GB              # never fill below this
  prune_old_versions: true
  notify: file                    # or email, or webhook later

profiles:
  home:
    inherit: [zims, youtube, kolibri, osm, calibre]
  travel:
    zims: [wikipedia_for_schools]
    youtube:
      - channel: https://www.youtube.com/@3blue1brown
        max_videos: 5
```

Schema validation runs on every load. A typo in a field name fails
fast with a useful error, not a silent partial run.

## Updater architecture

```
update.py
├── manifest loading + schema validation
├── policies (bandwidth, disk reserve, profiles)
├── plugins/                      # one per source type
│   ├── zim.py
│   ├── youtube.py
│   ├── kolibri.py
│   ├── osm.py
│   └── calibre.py
├── lib/
│   ├── lock.py                   # flock to prevent concurrent runs
│   ├── disk.py                   # preflight, reserve enforcement
│   ├── http.py                   # ETag-aware fetch, retry, resume
│   ├── reload.py                 # SIGHUP / API call to relevant service
│   └── report.py                 # markdown rendering
└── runner.py                     # orchestration loop
```

Each plugin implements the same interface (which mirrors the broader
adapter pattern from `identity.md`):

```python
class ContentPlugin(Protocol):
    name: str

    def plan(self, manifest, policies, state) -> list[Action]: ...
    def apply(self, action, policies) -> ActionResult: ...
    def prune(self, manifest, policies) -> list[ActionResult]: ...
    def reload_targets(self) -> list[ServiceName]: ...
```

`Action` and `ActionResult` are small dataclasses:

```python
@dataclass
class Action:
    plugin: str
    op: Literal["download", "import", "delete"]
    item_id: str
    description: str
    estimated_bytes: int | None
    metadata: dict

@dataclass
class ActionResult:
    action: Action
    status: Literal["ok", "skipped", "failed"]
    bytes_transferred: int
    duration_seconds: float
    error: str | None
```

## Run lifecycle

```
1. Acquire lock (/var/lock/treehouse-updater.lock).
   Bail if another run in progress.

2. Load manifest. Validate schema. Resolve profile (default: home).

3. Plan phase — every plugin returns its actions:
     plans = {p.name: p.plan(manifest, policies, state) for p in plugins}
   Total estimated bytes summed across all plans.

4. Preflight disk:
     free = df(content_disk)
     required = sum(plans) + policies.disk_reserve
     if free < required: abort with clear report.

5. If --dry-run: render report, exit. (No work done.)

6. Apply phase — plugins run in dependency order, with timeouts:
     for plugin in topo_sort(plugins):
       for action in plans[plugin.name]:
         result = plugin.apply(action, policies)
         report.record(result)

7. Prune phase — same order, after apply has fully succeeded:
     for plugin in plugins:
       for result in plugin.prune(manifest, policies):
         report.record(result)

8. Reload — for every service whose content changed, send the
   appropriate signal/API call so the running container picks up
   new files (e.g., kiwix-serve reloads its library).

9. Render and write report:
     logs/update-2026-05-05.md
     logs/latest.md  (symlink)

10. Release lock. Exit code reflects whether any action failed.
```

## Per-source plugins

### `zim` — Kiwix archives

**What's new mechanism:** parse the OPDS catalog at
`https://library.kiwix.org/catalog/v2/entries`. Compare published
filename (which encodes a date) against what's on disk.

**Apply:** `aria2c` download to `.tmp` filename with resumable
multi-connection. On completion, verify size and SHA-256 against
catalog metadata, then atomic rename to canonical name. Update
`library.xml` (used by kiwix-serve) so the new ZIM is picked up.

**Idempotency:** if `wikipedia_en_all_nopic_2026-04.zim` is already
present and the catalog says that's the latest, no action.

**Prune:** with `keep: latest`, delete prior versions only after the
new version's SHA verifies. With `keep: 2`, retain the previous
version too. Default policy `prune_old_versions: true`.

**Reload:** `kiwix-serve` honours `SIGHUP` to reload `library.xml`.
The plugin sends `docker kill -s HUP kiwix`.

### `youtube` — yt-dlp into PeerTube

**What's new mechanism:** `yt-dlp --download-archive
state/updater/youtube/archive.txt`. yt-dlp records every video ID it's
downloaded; on subsequent runs it skips them automatically.

**Apply:** two stages.

1. **Download to staging:**
   ```
   yt-dlp \
     --download-archive state/updater/youtube/archive.txt \
     --paths state/staging/youtube \
     --format "bestvideo[height<=720]+bestaudio/best[height<=720]" \
     --write-info-json --write-thumbnail \
     --rate-limit ${policies.bandwidth_limit} \
     --max-downloads ${channel.max_videos or unlimited} \
     --dateafter ${channel.since or 19700101} \
     <channel_url>
   ```
2. **Import to PeerTube:** for every new file in staging, call
   PeerTube's upload API:
   ```
   POST /api/v1/videos/upload
   Authorization: Bearer <admin-oauth-token>
   ```
   On success, delete the staging file. On failure, keep it; next run
   retries.

**Idempotency:** the `--download-archive` skips yt-dlp work; PeerTube's
`originallyPublishedAt` field plus channel-tag dedupe prevents
re-upload if the staging file is somehow re-encountered.

**Prune:** if a channel is removed from the manifest, remove all
videos in PeerTube tagged with that channel's import ID. (Safer than
deleting by URL — preserves manual additions.)

**Reload:** none needed; PeerTube auto-detects.

**Policy notes:**
- `--rate-limit` honours the global bandwidth cap.
- `--cookies` may be required for some channels; not a default.
- yt-dlp itself updates frequently (cat-and-mouse with YouTube).
  The container image needs a recent yt-dlp; rebuild monthly.

### `kolibri` — Khan and other channels

**What's new mechanism:** Kolibri's own `importchannel` and
`importcontent` are natively idempotent. They check what's already
present and only fetch new resources.

**Apply:** delegate to Kolibri itself.
```
docker exec kolibri kolibri manage importchannel network <token>
docker exec kolibri kolibri manage importcontent network <token>
```

**Idempotency:** Kolibri tracks resources by content node ID. Re-runs
are no-ops if nothing has changed upstream.

**Prune:**
```
docker exec kolibri kolibri manage deletechannel <token>
```
Run only for channels removed from manifest.

**Reload:** none; Kolibri picks up new content immediately.

**Source caveat:** Kolibri's content packs are downloaded from
`studio.learningequality.org`. The updater needs upstream network
access during these calls, which the isolated-mode VM doesn't have.
Two viable patterns: flip `network.mode` to `lan` for the duration of
the content fetch (sacrifices structural isolation while running), or
fetch on a separately-connected machine and rsync the artifacts onto
the box. A cleaner mechanism — a host-side proxy reachable from the VM
only during a defined maintenance window — is a future improvement.

### `osm` — OpenStreetMap regional extracts

**What's new mechanism:** `Last-Modified` HTTP header on Geofabrik's
`-latest.osm.pbf` URLs.

**Apply:** conditional GET (`If-Modified-Since`). If newer:

1. Download new `.pbf` to staging.
2. Run `tilemaker` to render `.mbtiles`.
3. Atomic swap into `content/osm/<region>.mbtiles`.
4. Reload tileserver.

**Idempotency:** the conditional GET is the gate. Tile rendering is
expensive and only happens when the source changed.

**Prune:** delete `.mbtiles` for regions removed from manifest.

**Reload:** `tileserver-gl` doesn't auto-reload mbtiles; restart the
container or send a config-reload API call (depends on tileserver
version).

### `calibre` — books from Standard Ebooks (and elsewhere)

**What's new mechanism:** parse the Atom feed; compare entry IDs
against `calibredb list --search "identifiers:..."`.

**Apply:** for each new entry matching the filter:

1. Download EPUB to staging.
2. `calibredb add --library-path /srv/treehouse/state/calibre staging/file.epub`

**Idempotency:** Calibre's identifier-based dedupe.

**Prune:** rarely needed. Books removed from the feed but already
added stay added. If manifest explicitly removes a book, run
`calibredb remove`.

**Reload:** none; calibre-web reads the metadata.db live.

## Bandwidth and concurrency

`policies.bandwidth_limit` is a global cap shared across all
plugins for a single run. The plan phase computes per-plugin shares;
the apply phase enforces them via the underlying tool's rate limit
flags (`aria2c --max-overall-download-limit`, `yt-dlp --rate-limit`,
`curl --limit-rate`, etc).

By default plugins run sequentially, not concurrently. Sequential
execution simplifies the bandwidth math, simplifies the report, and
avoids thrashing the disk with parallel large writes. Optimization
to parallel runs is deliberately deferred — the system is run once
a week at 3 AM; total wall time is not a hot path.

## Disk reserve and aborts

Before any download, the plan phase computes:

```
required = sum(estimated_bytes for action in all_plans)
free = df --output=avail /srv/treehouse/content
if free - required < policies.disk_reserve:
    abort with report explaining: free, required, reserve, deficit
```

Abort is a hard fail — no partial application. The operator either
adds storage, lowers the manifest's appetite, or accepts the prune
of older content.

`policies.disk_reserve` defaults to 50 GB. This is the headroom that
keeps the box healthy when a kid is mid-PeerTube-watch and Postgres
needs to flush. Don't go below 20 GB.

## Reports

Each run writes a markdown report:

```markdown
# Treehouse content update — 2026-05-05 03:00 UTC

**Status:** ✓ ok (3 actions, 0 failures, 14m 22s)
**Bandwidth:** 8.2 GB downloaded
**Disk:** 612 GB used / 1024 GB total

## Plan summary

| Plugin   | Actions | Bytes      |
|----------|---------|------------|
| zim      | 1 new   | 5.1 GB     |
| youtube  | 12 new  | 3.0 GB     |
| kolibri  | 0       | -          |
| osm      | 0       | -          |
| calibre  | 4 new   | 47 MB      |

## ZIM

- ✓ wikipedia_en_all_nopic_2026-04.zim (5.1 GB, 12m 31s)

## YouTube

- ✓ @3blue1brown — 3 new videos (...)
- ✓ @veritasium — 1 new video (...)
- ✓ @kurzgesagt — 8 new videos (...)
- ✗ @SmarterEveryDay — error fetching channel
  (HTTP 429, retried 3 times). Will retry next run.

## Kolibri / OSM
No changes.

## Calibre
- ✓ Added 4 books matching tags=children
  (titles redacted)

## Pruned
- 🗑 wikipedia_en_all_nopic_2026-03.zim (4.8 GB)

## Errors
1 transient (yt-dlp 429 on one channel). Non-fatal.
```

`logs/latest.md` is symlinked to the most recent report. The launcher's
`admin.kids` page renders it inline so Mick can read the week's
update without ssh.

## Scheduling

systemd timer, weekly:

```ini
# /etc/systemd/system/treehouse-update.timer
[Unit]
Description=Weekly content refresh for Treehouse

[Timer]
OnCalendar=Sun 03:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/treehouse-update.service
[Unit]
Description=Run Treehouse content updater
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/srv/treehouse/updater/.venv/bin/python -m updater
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
TimeoutStartSec=12h
```

`Nice=10` and a low IO priority matter — content updates must not
starve a kid mid-stream if anything happens to be active at 3 AM.

`TimeoutStartSec=12h` is a hard ceiling. A run that exceeds it is
either making real progress on a large download or hung; either way,
operator review.

## Manual operations

```
make manifest-plan          # updater --dry-run, show plan
make manifest-apply         # updater, do the work
make manifest-apply ONLY=zim
make manifest-apply --no-prune
```

The `ONLY=` flag restricts to a single plugin. Useful when one
plugin is broken and shouldn't block the others, or for surgical
top-up of a single content type.

## Open questions

- **Should yt-dlp be replaced by a less-fragile alternative?** YouTube
  changes break yt-dlp constantly. Possible alternatives: piped.video
  scraping, an Invidious instance fronting the work. Defer until the
  yt-dlp pain level justifies it. Operationally the answer is "rebuild
  the container monthly" until proven inadequate.
- **Should there be an in-product way for Mick to add a YouTube
  channel from a kid-friendly bookmarking flow?** ("Daughter found a
  cool channel; one click to add it.") Possible Phase 11 feature.
  Until then, edit the YAML.
- **Should the updater support sources beyond the listed five?** Yes,
  via the plugin interface — a future `mit-ocw`, `bbc-bitesize`,
  `nature-podcast` plugin is expected. Adding one is a self-contained
  module.
