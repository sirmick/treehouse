# Treehouse

A safe, offline, walled-garden "internet" for kids. Self-hosted on a private
WiFi segment with no upstream access, stocked with vetted educational and
entertainment content drawn from open archives, and orchestrated by a small
launcher that handles identity, search, and a learning-companion AI.

> Status: design phase. Architecture decided; implementation has not started.
> Plans are being written before code lands.

## Why

Mainstream "kids' modes" on consumer platforms either gate behind ad-driven
ecosystems, leak into the open web at the edges, or replace one set of
distractions with another. Treehouse takes a different approach: an entirely
disconnected network whose contents are explicitly chosen, where the failure
mode of "the kid finds something we didn't expect" is structurally impossible
because there is no path off the box.

The aim is not to keep kids in a bubble forever. It is to give them a
home-shaped corner of the internet where curiosity is rewarded, the materials
are good, and parents can graduate them outward on their own timeline.

## What's in it

A box (VM today, Raspberry Pi later) running:

- **Kiwix** with offline ZIMs of Wikipedia, Wiktionary, Project Gutenberg,
  Stack Exchange, TED, and similar archives.
- **Kolibri** for Khan Academy and other structured learning content with
  per-kid progress tracking.
- **Sugarizer**, the web port of OLPC's Sugar learning Activities.
- **PeerTube** as a local YouTube-shaped video service, pre-seeded with
  curated channels via `yt-dlp`.
- **Calibre-web** for ebooks.
- **OpenStreetMap** tile server for offline maps.
- **Synapse** (Matrix) for LAN-only family/friend messaging, federation off.
- **Treehouse's own services**: launcher (identity + UX), `searchd`
  (per-source ingestors + a thin read shim over a hybrid MeiliSearch index;
  serves both the launcher search bar and the AI's RAG retrieval),
  `aigateway` (RAG-only local LLM as a learning companion), `updater`
  (idempotent content sync from a declarative manifest), `provisioner`
  (declarative kid-account management).

## Architecture summary

```
              ┌──────────────────────────────────────────────┐
   kids' ──── │  WiFi AP (client-isolated, no upstream)      │
   devices    └────────────────────┬─────────────────────────┘
                                   │ bridge
                                   ▼
              ┌──────────────────────────────────────────────┐
              │  Host (Debian VM, later RPi 5):              │
              │   dnsmasq · nginx · nftables · Docker        │
              │                                              │
              │   Containers:                                │
              │    kiwix · kolibri · sugarizer · peertube    │
              │    synapse · calibre · tileserver · ollama   │
              │    meilisearch · launcher · searchd          │
              │    aigateway                                 │
              │                                              │
              │   /srv/treehouse/                            │
              │     ├── config/   (in git)                   │
              │     ├── state/    (small, backed up)         │
              │     └── content/  (huge, replaceable)        │
              └──────────────────────────────────────────────┘
```

Two declarative source-of-truth files drive the whole system:

- **`manifest.yml`** — what content lives on the box (ZIMs, YouTube channels,
  Kolibri channels, OSM regions, books). The updater reconciles disk to match.
- **`kids.yml`** — who the kids are, ages, permissions, contacts. The
  provisioner reconciles each backend service's user accounts to match.

Both are version-controlled. State (Sugarizer journals, Kolibri progress,
Matrix history) is backed up nightly via restic. Content is replaceable from
manifest, so it isn't backed up — just re-fetched.

## Identity model

Vaulted SSO, not federated. The launcher is the identity authority. Each
kid picks an avatar, optionally enters a PIN, and the launcher mints session
tokens for each backend on their behalf using stored admin credentials. nginx
ensures direct service access (`khan.kids/...`) without a `kidsession` cookie
gets bounced through the launcher first, so the broker is unbypassable.

## AI design

A small local LLM (Llama 3.2 3B class) runs offline via Ollama. It is wired
in **RAG-only** — it never recalls facts from training, only synthesizes from
search results retrieved by `searchd`. The system prompt is Socratic and
age-banded: ask what the child knows, point them at sources, never give
direct answers, never discuss off-library topics. Topic gates, short context
windows, and parent-review logging are structural guardrails.

## Documentation

Design docs live in [`docs/`](docs/):

- [architecture.md](docs/architecture.md) — system overview, decisions, rationale
- [roadmap.md](docs/roadmap.md) — phased build plan
- [deployment.md](docs/deployment.md) — libvirt VM, Ansible roles, Docker, storage, sizing
- [network.md](docs/network.md) — AP, DHCP, DNS, isolation
- [content.md](docs/content.md) — manifest, updater, per-source idempotency
- [identity.md](docs/identity.md) — kids.yml, broker, adapter contract
- [launcher.md](docs/launcher.md) — UI, screens, test strategy
- [search.md](docs/search.md) — aggregator, schema, ranking
- [ai.md](docs/ai.md) — local AI, RAG, prompts, guardrails
- [operations.md](docs/operations.md) — backup, observability, maintenance
- [books.md](docs/books.md) — planned ebook track (Calibre-web + Standard Ebooks)
- [MILESTONE-1.md](docs/MILESTONE-1.md) — operator runbook for the first runnable slice
- [commands.md](docs/commands.md) — every `make` target, what it does, when to run it

## License

[AGPL-3.0-or-later](LICENSE). If you fork Treehouse and run it as a service
for others, your modifications must be published under the same terms.
