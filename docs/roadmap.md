# Roadmap

A phased build plan, ordered so each phase produces something demonstrable
and builds on the last. The goal at every phase is a system you could put
in front of a kid, not a half-finished framework.

The phasing is opinionated: do the boring infrastructure first so that
the interesting parts (search, AI, launcher polish) can be tested against
real services rather than mocks.

## Phase 0 — Bootstrap

**Goal:** Empty Debian VM brought up reproducibly, with the host-level
plumbing in place, ready for content services to drop into.

**Deliverables:**

- `Vagrantfile` (libvirt or VirtualBox) provisioning a Debian 12 VM with
  two virtual disks (~40 GB system, ~1 TB content).
- `ansible/site.yml` with `base` and `network` roles:
  - apt baseline, ufw/nftables, ssh hardening
  - dnsmasq installed, configured to wildcard `.kids` to host IP
  - Caddy installed with a single placeholder vhost
  - Docker engine installed
  - Storage layout `/srv/treehouse/{config,state,content,launcher,logs}`
    created with correct ownership.
- `make up` brings the box from zero to bootable; `make destroy` cleans up.

**Exit criteria:**

- `vagrant up` completes without error.
- A device on the kids' bridge gets a DHCP lease.
- `curl http://hello.kids` from that device hits Caddy's placeholder page.
- `ip route` on the VM has no default route to the public internet
  (or the route exists only on a separate management interface).

## Phase 1 — Single-service MVP (Kiwix)

**Goal:** A kid can open a tablet, connect to the kids' WiFi, type
`wikipedia.kids` into the browser, and read Wikipedia for Schools entirely
offline.

**Deliverables:**

- `compose.yml` with `kiwix` and a `caddy-config` entry pointing
  `wikipedia.kids` → `kiwix:8080`.
- A "Wikipedia for Schools" ZIM (5 GB) downloaded manually into
  `content/zims/` — no updater yet.
- Caddy serves `home.kids` from a static placeholder page with a single
  link to `wikipedia.kids`.

**Exit criteria:**

- Both `home.kids` and `wikipedia.kids` reachable from a tablet.
- Internet truly unreachable: confirm via direct-IP probes (e.g.
  `curl 1.1.1.1` from the tablet hangs and times out).
- Restart the VM; everything still works without manual intervention.

This is the demo-to-yourself moment that proves the topology.

## Phase 2 — Launcher v1 + identity broker

**Goal:** Kid lands on `home.kids`, picks an avatar tile, taps a service,
and is logged in seamlessly. Add Kolibri as the second service to prove the
broker generalizes beyond one backend.

**Deliverables:**

- Launcher (SvelteKit + TypeScript, FastAPI broker backend):
  - `/` avatar grid
  - `/login` PIN entry (skip if `pin: null`)
  - `/home` tile launcher
  - `/api/launch/<service>` broker redirect endpoint
  - `/admin` HTTP-basic gated, lists adapter health
- Adapter contract module with two implementations:
  - `kiwix` (no users, just health)
  - `kolibri` (provisions facility users, mints session cookies)
- `kids.yml` declarative spec, with one kid for testing.
- `provisioner` script that reads `kids.yml` and calls
  `users_ensure` on every adapter.
- Caddy rule: any request to a service vhost without a `kidsession`
  cookie is redirected to `home.kids/launch?to=<service>`.
- Test stack: Vitest for launcher components, Playwright for end-to-end
  ("Alice picks tile → enters PIN → lands authenticated in Kolibri").

**Exit criteria:**

- A test kid in `kids.yml` is auto-provisioned in Kolibri on first
  `provisioner` run.
- E2E test: avatar → PIN → Kolibri logged in, all in CI.
- Direct-access test: navigating to `khan.kids` without a session
  bounces back through the launcher and arrives logged in.
- Re-running the provisioner is idempotent (no duplicate users, no errors).

## Phase 3 — Content updater MVP

**Goal:** Replace the manual ZIM placement with a declarative,
idempotent content sync.

**Deliverables:**

- `manifest.yml` schema (initial: ZIMs only).
- `updater` Python CLI with:
  - Manifest schema validation
  - Per-source plugin interface (`content_plan`, `content_apply`,
    `content_prune`)
  - `zim` plugin: parses Kiwix OPDS catalog, diffs against disk,
    downloads via `aria2c`, atomic swap.
  - `--dry-run` showing planned actions and total size.
  - End-of-run report rendered as `logs/update-YYYY-MM-DD.md`.
- systemd timer (weekly) running `updater` as a oneshot.

**Exit criteria:**

- Adding a new ZIM to the manifest and running `updater` downloads only
  that ZIM, leaves others untouched.
- Removing a ZIM from the manifest and running `updater --prune`
  removes it from disk, after the new version (if any) is verified.
- Re-running `updater` with no manifest changes does zero downloads.
- Disk-reserve preflight aborts cleanly if free space is below the
  configured threshold.

## Phase 4 — More content services

**Goal:** Round out the read-and-make stack: Sugarizer (Activities),
Calibre-web (ebooks), OSM tile server (maps).

**Deliverables:**

- Compose entries and Caddy vhosts for each.
- Adapters:
  - `sugarizer` (its own user model, tokens stored in launcher)
  - `calibre-web` (HTTP basic via launcher-injected header)
  - `tileserver` (no users)
- Updater plugins:
  - `kolibri` (importchannel via `docker exec`)
  - `osm` (geofabrik download + tilemaker rebuild)
  - `calibre` (Standard Ebooks Atom feed → calibredb add)
- Launcher home tiles populated for the new services.

**Exit criteria:**

- All five content services reachable, each accessed via the broker.
- Updater can refresh content for any of them via manifest edits.
- Kid created in `kids.yml` exists in Sugarizer, Kolibri, and
  Calibre-web after running provisioner.

## Phase 5 — Federated search

**Goal:** A kid types "volcanoes" once and sees results from every
content service grouped by kind.

**Deliverables:**

- `searchd` FastAPI service:
  - `GET /api/search?q=...&kid_id=...`
  - Concurrent fan-out with per-source timeouts
  - Normalized result schema
  - Per-kid age-band filtering and history bias
  - Cross-source dedupe
  - 24-hour query cache (sqlite)
- Launcher `/search` route consuming searchd.
- Result UI grouped by `kind`: Read / Watch / Learn / Play.
- Per-kid search history persisted in `state/searchd/`.

**Exit criteria:**

- A query returns results from at least Kiwix, Kolibri, and Calibre
  in a single response in under 1 second on warm cache.
- A failing backend (e.g. Kolibri stopped) does not break the response;
  it shows "0 results from Kolibri" and continues.
- Age-band filter demonstrably reorders results for a 6-year-old vs an
  11-year-old test profile.

## Phase 6 — Video (PeerTube + YouTube import)

**Goal:** A curated, vetted local copy of selected YouTube channels,
served via PeerTube, importable by manifest.

**Deliverables:**

- PeerTube + Postgres + Redis in compose.
- Federation disabled, registration disabled.
- Adapter: `peertube` (admin OAuth, user provisioning, video import).
- Updater plugin: `youtube`:
  - Per-channel `yt-dlp --download-archive`
  - Format/resolution caps from manifest
  - Stage → upload via PeerTube API → delete staging on success
- Launcher tile for video.

**Exit criteria:**

- Adding a YouTube channel to `manifest.yml` causes the next updater
  run to import that channel's recent videos.
- Re-running does not re-import already-present videos.
- Removing a channel is honoured by `--prune`.
- Kid logs into `videos.kids` via broker, history persists per kid.

## Phase 7 — Messaging (Matrix)

**Goal:** Family/contacts chat in a closed room set, encryption off
(LAN-only, no value), provisioning driven by `kids.yml`.

**Deliverables:**

- Synapse + Postgres in compose; federation off; registration off.
- Element-web served from `chat.kids`.
- Adapter: `synapse` (admin API for users, login API for tokens).
- Per-kid contact lists from `kids.yml.permissions.messaging_contacts`
  reified as Matrix room invitations.
- Launcher injects Matrix access token into Element's localStorage on
  redirect (intermediate page pattern).

**Exit criteria:**

- Two kids defined in `kids.yml` can message each other.
- A kid whose `messaging: false` cannot reach `chat.kids` (broker refuses).
- Re-running provisioner does not duplicate or break existing rooms.

## Phase 8 — Local AI companion

**Goal:** A Socratic learning companion that helps a kid explore the
library without doing their thinking for them.

**Deliverables:**

- Ollama container with one small model (Llama 3.2 3B class).
- `aigateway` service:
  - `/api/ask` endpoint
  - RAG against `searchd` for every query
  - System prompt assembled from age-band + retrieved sources
  - Topic-gate classifier (allowlist / denylist) on input
  - Short-context policy (clear after N turns)
  - Logs every interaction to `state/aigateway/transcripts.sqlite`
- Launcher AI tile.
- Admin dashboard page: "What did Alice ask the AI this week?"

**Exit criteria:**

- Ask a question with a known library answer → response cites sources.
- Ask a question outside the library → response says so, doesn't bluff.
- Ask an off-topic / inappropriate question → topic gate refuses without
  invoking the model.
- Parental review page shows last 50 interactions per kid with sources.

## Phase 9 — Raspberry Pi migration

**Goal:** Same Ansible roles, same Compose stack, running on dedicated
hardware that lives on the shelf.

**Deliverables:**

- Pi 5 + NVMe HAT + 1 TB NVMe + case + UPS HAT.
- Pi-specific Ansible inventory.
- `pi-gen` configuration baking a ready-to-flash image (or a
  documented "first boot" path).
- Hostapd config if the Pi serves WiFi directly, OR documented
  GL.iNet AP config if external.
- Migration runbook: snapshot VM state → copy to Pi → boot.

**Exit criteria:**

- Pi boots from cold, comes up healthy, all services pass health checks.
- Restic restore of state from VM-era backups completes cleanly.
- VM can be powered off; family uses the Pi exclusively for a week
  without regressions.

## Phase 10 — Travel form factor

**Goal:** A subset of Treehouse goes on the road. Grandma's house, road
trips, holidays.

**Deliverables:**

- Decision: travel-router-with-everything (GL.iNet Slate AX as host) vs.
  tablet-with-cached-content (PWA / installable launcher offline mode).
- Either way: a `--profile=travel` for the manifest that selects a
  smaller content set.
- Optional: a sync mechanism between the home box and the travel device.

**Exit criteria:**

- Documented and tested: take the device offline, verify everything works
  for at least the planned trip duration.

## Cross-cutting work that runs alongside

Some workstreams don't fit a single phase and progress incrementally:

- **Test coverage.** Every phase ships with tests. The launcher's
  E2E suite grows from Phase 2 onward and gates merges from Phase 3
  onward.
- **Backup discipline.** Restic configured in Phase 0 with `state/`
  empty; verified to work end-to-end (snapshot → restore → boot)
  every time a new stateful service is added.
- **Documentation.** Each design doc in `docs/` is updated as the
  corresponding phase lands. A doc that doesn't match the code is a
  bug.
- **Curation.** `manifest.yml` and `kids.yml` evolve continuously;
  expect to revisit at least monthly. The kid's interests change
  faster than the architecture.

## What is deliberately not scheduled

- A polished kid-launcher visual design. The Phase 2 launcher is
  functional, not pretty. Visual design happens after Phase 8 once the
  full feature set is in place; designing the chrome before the
  features are stable is wasted work.
- Multi-kid features beyond `kids.yml` plumbing. Until there is a
  second kid (sibling, friend), one-kid is the optimization target.
- An "admin app" beyond the basic dashboard. Editing YAML files is the
  admin UX for now. A real admin app is a Phase 11+ concern.
