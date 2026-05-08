# Architecture

The big-picture view of Treehouse: how the pieces fit together, why they
fit that way, and where the seams are.

## Goals shaping the design

In rough priority order, the choices below were optimized for:

1. **Structural safety, not promised safety.** "The kids cannot reach the
   open internet" must be a property of the network topology, not of a config
   flag that can flip. No upstream route exists from the kids' segment.
2. **Reproducible from declarative source.** A fresh box plus `manifest.yml`,
   `kids.yml`, and a restic restore of `state/` reproduces the system. No
   irreplaceable hand-tuning.
3. **Testability.** Every behaviour the kids can observe should be reachable
   from an automated test. The launcher in particular is designed
   test-first.
4. **Maintenance amortized over years.** This will run for ~a decade as kids
   age through it. Designs that are clever today but high-friction to update
   are rejected in favour of boring designs that are easy to revisit.
5. **Minimal commitment to any one component.** Adapter pattern everywhere
   so swapping Kolibri for some future learning platform is a single new
   module, not a rewrite.

## Topology

```
   ┌──────────────────────────────────────────────────────────────┐
   │  KIDS' SEGMENT                                               │
   │                                                              │
   │   [tablet]  [tablet]  [TV]       (no path to internet)       │
   │      │         │       │                                     │
   │      └─────────┴───────┴────────► [WiFi AP, isolated]        │
   │                                          │                   │
   └──────────────────────────────────────────┼───────────────────┘
                                              │ bridged ethernet
                                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  TREEHOUSE HOST  (Debian VM today, RPi 5 later)              │
   │                                                              │
   │   On systemd:                                                │
   │     dnsmasq      DHCP + DNS authority for the segment        │
   │                  Wildcards *.kids → host IP                  │
   │     nginx        Reverse proxy, vhosts per service           │
   │                  Brokers cookies/tokens through redirects    │
   │     nftables     Drop-all upstream rule (defense in depth)   │
   │     restic       Nightly backup of state/ to external disk   │
   │                                                              │
   │   In Docker Compose:                                         │
   │     ┌──────────────┬──────────────┬─────────────────┐        │
   │     │ Content      │ Identity     │ Treehouse       │        │
   │     │ services     │ services     │ services        │        │
   │     │              │              │                 │        │
   │     │ kiwix        │ synapse      │ launcher        │        │
   │     │ kolibri      │   + postgres │ searchd         │        │
   │     │ sugarizer    │ element      │ aigateway       │        │
   │     │   + mongo    │ (matrix      │ updater (cron)  │        │
   │     │ peertube     │  client)     │ provisioner     │        │
   │     │   + postgres │              │   (one-shot)    │        │
   │     │   + redis    │              │ ollama          │        │
   │     │ calibre-web  │              │                 │        │
   │     │ tileserver   │              │                 │        │
   │     └──────────────┴──────────────┴─────────────────┘        │
   │                                                              │
   │   /srv/treehouse/                                            │
   │     config/   (in git: compose.yml, nginx.conf, dnsmasq, ...) │
   │     state/    (DB volumes, journals, credentials cache)      │
   │     content/  (ZIMs, videos, tiles, books — replaceable)     │
   │     launcher/ (built static assets)                          │
   └──────────────────────────────────────────────────────────────┘
                                              ▲
                                              │ ssh (admin only,
                                              │  separate VLAN)
                                       [Mick's workstation]
```

## Stack split: host vs containers

The split is governed by two questions:

- **Does it need host-level network privilege?** dnsmasq broadcasts DHCP
  packets the segment must answer; nginx binds privileged ports for the
  reverse proxy. Both are simpler on the host.
- **Is it stateful infrastructure vs. application logic?** Plumbing on the
  host; everything else in containers.

### On the host

| Service | Role |
|---|---|
| dnsmasq | DHCP + DNS authority for the kids' segment; wildcards `.kids` to host IP |
| nginx | Reverse proxy, per-vhost routing, broker-redirect rules, optional internal CA |
| nftables | Drop-all-upstream rule (the kids' segment has no default route, and this is belt + suspenders) |
| docker engine | Container runtime |
| chrony | Time sync (no upstream NTP — see operations.md) |
| restic | Backup runner (systemd timer, nightly) |

### In Docker Compose

| Container | Image | Role |
|---|---|---|
| kiwix | `ghcr.io/kiwix/kiwix-serve` | Serves all ZIM files |
| kolibri | `learningequality/kolibri` | Learning platform with per-kid progress |
| sugarizer | `sugarizer/sugarizer` | OLPC Activities |
| mongo-sugarizer | `mongo:6` | Sugarizer's data store |
| peertube | `chocobozzz/peertube` | Local video |
| postgres-peertube | `postgres:15` | PeerTube DB |
| redis-peertube | `redis:7` | PeerTube job queue |
| synapse | `matrixdotorg/synapse` | Matrix homeserver |
| postgres-synapse | `postgres:15` | Synapse DB (separate, do not share) |
| element | `vectorim/element-web` | Matrix client |
| calibre-web | `linuxserver/calibre-web` | Ebook reader |
| tileserver | `maptiler/tileserver-gl` | OSM map tiles |
| ollama | `ollama/ollama` | Local LLM serving |
| launcher | (Treehouse) | Identity broker + kid UX |
| meilisearch | `getmeili/meilisearch` | Hybrid (BM25 + vector) index over all content; fed by searchd's ingestors, queried by both the launcher search bar and the AI gateway |
| searchd | (Treehouse) | Per-source ingestors + thin read shim over MeiliSearch (kid-aware filters, ranking adjustments, activity log) |
| aigateway | (Treehouse) | RAG orchestration in front of Ollama |
| updater | (Treehouse) | Content sync, runs as oneshot via systemd timer |
| provisioner | (Treehouse) | Kid account sync, runs on `kids.yml` change |

## Data flow: a kid's day

```
1. Kid opens browser → home.kids
   └─► nginx → launcher container
       └─► launcher renders avatar grid

2. Kid taps "Alice" → enters PIN
   └─► launcher sets signed `kidsession` cookie scoped to .kids
   └─► launcher renders home tiles

3. Kid taps "Khan Academy"
   └─► launcher /api/launch/kolibri
       ├─► looks up Alice's Kolibri credentials in credentials.sqlite
       ├─► POSTs Kolibri /api/auth/session/, captures sessionid cookie
       ├─► returns 302 to khan.kids with Set-Cookie for that session
       └─► nginx proxies → kolibri container

4. Kid searches "volcanoes" in launcher
   └─► launcher → searchd /api/search?q=volcanoes&kid_id=alice
       ├─► hybrid (BM25 + vector) query against MeiliSearch
       ├─► reranks by Alice's age band + click history
       └─► returns grouped results (Read / Watch / Learn / Play)

5. Kid asks the AI companion "why do volcanoes erupt"
   └─► launcher → aigateway /api/ask
       ├─► aigateway calls searchd to retrieve relevant sources
       ├─► builds prompt: system + age-band + retrieved snippets + query
       ├─► calls ollama
       ├─► post-filters response, logs (kid_id, query, sources, response)
       └─► returns Socratic response with source links

6. Kid leaves; "switch user" or 30 min idle timeout
   └─► launcher invalidates kidsession + service-side sessions best-effort
   └─► back to avatar grid
```

## Key design decisions

### Vaulted SSO, not federated

The kids' service set is heterogeneous: Kolibri, Sugarizer, and Calibre-web
have no native OIDC support. Implementing real federated SSO would require
writing OIDC bridges for each, which is weeks of work for a one-family
deployment. Instead, the launcher acts as a credential vault: it knows each
kid's credentials in each service and brokers logins on their behalf via
each service's native auth API. From the kid's perspective it is SSO; from
the architecture's perspective it is a centralized credential broker.

The trade-off: logout-everywhere is best-effort, and the launcher must
implement an adapter per service. Both are acceptable. See
[identity.md](identity.md) for the full reasoning.

### RAG-only local AI

Open-weights small models (3–8B parameters) hallucinate confidently. A
confidently-wrong answer in a learning context is worse than no answer.
Treehouse's AI is wired so the model **never recalls facts from training** —
it only synthesizes responses from search results retrieved by `searchd`
(via MeiliSearch's hybrid index) inside the box's own library. If asked
about something not in the library, it says so. This is enforced at the
gateway layer, not by the prompt alone.

See [ai.md](ai.md) for prompt design and guardrails.

### Manifest-driven content, idempotent updater

Content is huge (1+ TB) and changes constantly (YouTube channels publish
new videos, ZIMs re-publish monthly). The updater is driven by
`manifest.yml` and uses each source's native "what's new" mechanism to
avoid re-downloading: ZIMs via the Kiwix OPDS catalog, YouTube via
`yt-dlp --download-archive`, Kolibri via `importchannel` (idempotent
natively), OSM via `Last-Modified` headers. A weekly systemd timer is
sufficient.

Content is *not* backed up. State is. Loss of content is recoverable from
manifest; loss of state (Sugarizer journals, Matrix history, Kolibri
progress) is not. See [content.md](content.md) and
[operations.md](operations.md).

### Adapter pattern everywhere

Every backend service has an adapter implementing eight verbs:

```
content_plan(manifest)   content_apply(plan)   content_prune(manifest)
users_ensure(kids)       users_remove(kid_id)
auth_token(kid_id)
health()                 reload()
```

Three orchestrators iterate over the same adapter list:

- **updater** uses the content verbs
- **provisioner** uses the identity verbs
- **admin dashboard** uses `health()`

Adding a new service later (e.g. a self-hosted Scratch instance) is just
dropping an adapter module — all three orchestrators pick it up. No
service-specific code paths in the orchestrators.

### Two declarative files, version controlled

- `manifest.yml` — content desired on the box
- `kids.yml` — kids and their permissions

Everything else is derived. This is the property that makes the system
reproducible and the main reason to resist letting any non-declarative
state leak in. (Deviations: each service holds its own progress/state in
its own DB. That state is *output*, not *input*, and is backed up
separately.)

## What is intentionally out of scope

- **Public-internet hosting.** A previous design considered a Route53 demo
  face. Decided against. Treehouse is offline-only.
- **Multi-tenant / school deployments.** That is what IIAB and RACHEL exist
  for. Treehouse is one family.
- **Federated SSO via OIDC.** Vaulting is sufficient and avoids per-service
  bridges.
- **Federation with the wider PeerTube / Matrix networks.** Both are
  configured `federation: false`.
- **An app store / installable Activities marketplace.** New Activities are
  added by editing the manifest. No in-product discovery surface.

## Where complexity is permitted to live

- **The launcher.** It is the kid-facing surface and the broker between
  every other service. It earns the right to be the most carefully
  designed component.
- **The updater.** Content sync is the operational backbone. Idempotency,
  bandwidth caps, dry-run, and clear reporting matter more here than
  almost anywhere else.

## Where complexity is rejected

- **The host configuration.** It should be small enough to read in one
  sitting. dnsmasq + nginx + nftables, not Kubernetes.
- **Inter-service communication.** Containers talk over HTTP on a private
  Docker network. No message bus, no service mesh.
- **Auth.** The vault is sqlite; the broker is a redirect. There is no
  IdP, no JWT issuer, no OIDC discovery.
