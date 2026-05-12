# Deployment

How Treehouse is built and run, from `make up` on a developer laptop
through to the Raspberry Pi that lives on the shelf in the family room.

The same Ansible roles and Compose stack target both. The only differences
between the VM and Pi deployments live in inventory variables (network
interface names, hostapd vs. external AP, image format) — never in
service configuration.

## Targets

| Target | Purpose | Hardware | When |
|---|---|---|---|
| libvirt VM | Iteration, CI, tests | KVM via virt-install on a Linux laptop | All development |
| `pi` | Production family deployment | Raspberry Pi 5 + NVMe | After Phase 9 |
| `cloud` | Not supported | — | Explicitly out of scope |

The `cloud` row is here as a reminder: an earlier design considered a
public-internet demo face. Decided against. Treehouse is offline-only.
See `architecture.md` for the reasoning.

## Repository layout

```
treehouse/
├── bin/                            # shell entry points
│   ├── up.sh                       # libvirt + virt-install + cloud-init
│   ├── down.sh                     # tear down the VM
│   ├── verify-isolation            # the probe that runs inside the tablet VM
│   ├── verify-isolation-via-tablet.sh   # host-side wrapper
│   ├── test-live.sh                # pytest against the deployed VM
│   ├── launcher-config-gen         # writes launcher/src/lib/config.ts from yaml
│   └── cfg                         # tiny YAML reader for shell consumers
├── Makefile                        # all targets (see docs/commands.md)
├── treehouse.example.yml           # committed template for per-deployment infra
├── treehouse.yml                   # gitignored; copy from .example.yml and edit locally
├── manifest.yml                    # content (declarative; Phase-3 updater target)
├── kids.yml                        # kids (declarative; planned, schema in schemas/)
├── compose.yml                     # all containers
├── compose.test.yml                # smaller content, deterministic fixtures
├── nginx/
│   └── nginx.conf                  # mounted into the compose proxy container
├── launcher/                       # SvelteKit + Tailwind frontend
│   ├── src/                        # svelte components, types
│   └── build/                      # produced by `make launcher-build`
├── ansible/
│   ├── site.yml                    # top-level playbook (loads ../treehouse.yml)
│   ├── inventory/                  # written per-host by bin/up.sh
│   ├── group_vars/
│   │   └── all.yml                 # derives legacy flat names from treehouse.yml
│   ├── roles/
│   │   ├── base/                   # apt baseline, ssh
│   │   ├── network/                # dnsmasq (isolated mode), nftables, kids iface
│   │   ├── proxy/                  # host nginx + per-service vhosts (j2 template)
│   │   ├── docker/                 # docker-ce + compose plugin
│   │   ├── treehouse-services/     # copies compose.yml + nginx tree, starts stack
│   │   └── backup/                 # restic config + systemd timer
│   └── files/
│       └── br-kids.xml             # libvirt isolated network definition
├── treehouse/                      # Treehouse's own services (Phase 2+)
│   ├── adapters/                   # shared adapter modules (kiwix exists)
│   └── provisioner/                # CLI: kid account sync (planned)
├── schemas/                        # pydantic schemas for manifest.yml and kids.yml
├── docs/                           # design docs (this directory)
└── tests/                          # pytest: schemas, compose, live, etc.
```

The `treehouse/` Python packages and the SvelteKit launcher live in the
same repo so refactors that span the broker contract and the UI happen as
single PRs. Mono-repo for simplicity; not a polyglot zoo.

## libvirt + virt-install — the default development target

`bin/up.sh` is the entry point (driven by `make up`). It uses
libvirt directly via `virt-install` and a cloud-init NoCloud seed —
no Vagrant dependency.

What it sets up:

- Debian 12 generic-cloud image, cached at
  `/var/lib/libvirt/images/treehouse/debian-12-genericcloud-amd64.qcow2`.
- 40 GB system disk + 200 GB sparse content disk (qcow2 overlays).
- Single NIC, mode-dependent:
  - `isolated` (the default) — libvirt's `br-kids` network, an
    isolated bridge with no upstream and no NAT. The VM's dnsmasq
    serves DHCP/DNS on `10.10.10.0/24`. The laptop reaches the VM
    via qemu-guest-agent over virtio-serial, not over the network.
  - `lan` — libvirt's `lan` network, which bridge-forwards onto the
    host-managed `br-lan` (a Linux bridge spanning the host's
    physical NICs). The VM gets a static IP on the LAN; LAN devices
    reach it directly, and host↔VM works. dnsmasq is disabled in
    this mode. One-time host setup is in `network.md`.
- Cloud-init seeds the SSH key (auto-detected from `~/.ssh/`) so the
  laptop can `ssh mick@<vm>` without a password.

`bin/up.sh` is idempotent: re-running with the VM already defined is a
no-op. `bin/down.sh` (`make down`) destroys the VM and the per-VM
overlay disks. The base image is preserved for the next `make up`.

## Development DNS

The architecture rests on nginx doing host-header routing —
`wikipedia.kids`, `khan.kids`, `home.kids`, all on port 80. Falling
back to `localhost:8080`-style URLs in dev is rejected: the broker's
cookie scoping, redirect rules, and unbypassable bounce all depend
on real `.kids` hostnames. The dev environment must resolve them too.

### Per-domain resolver on the developer's laptop

systemd-resolved supports a routing-only rule: only `.kids` queries
go to the box; everything else uses the laptop's normal resolver. No
global DNS hijack, no `/etc/hosts` maintenance.

```ini
# /etc/systemd/resolved.conf.d/treehouse.conf
[Resolve]
DNS=10.10.10.1
Domains=~kids
```

After `systemctl restart systemd-resolved`: `home.kids` resolves via
the box, `github.com` continues to resolve via the laptop's upstream.

This requires an L2 path from the laptop to 10.10.10.1, which
libvirt's `private_network` provides automatically via the `virbr*`
interface created on the host.

### Tablet VM as the validation surface

The laptop is the wrong place to test "the kids segment cannot reach
the internet" — the laptop has its own internet, and even with the
per-domain resolver its routing table still has a default route. A
host-side netns sharing the host kernel is also too weak: routing
tables, conntrack, and fwmarks all leak from the host's view.

`bin/verify-isolation-via-tablet.sh` (driven by `make verify-isolation`)
spins up a throwaway "tablet" VM on demand: single NIC on `br-kids`,
no management interface, no SSH. cloud-init drops `bin/verify-isolation`
into the guest, runs it on first boot, and writes the output to a
file-backed serial. The host wrapper polls the serial for a sentinel,
prints results, and destroys the VM. Console log preserved at
`/var/lib/libvirt/images/treehouse/treehouse-tablet-console.log` for
forensics on failure.

Its DHCP comes from the box's dnsmasq. Its routing table has no
default route. It is the closest dev-time approximation of a tablet
on the kids' WiFi — and it's the gate, not a script run on the
developer's laptop. The gate is meaningful only in
`network.mode: isolated`; `lan` mode skips it with a clear message.

### Quick shell checks

For one-off curl from outside any VM:

```bash
curl --resolve home.kids:80:10.10.10.1 http://home.kids/
```

Useful in CI runners and ad-hoc debugging where systemd-resolved
isn't configured. Not a development pattern — use the resolver rule
for actual browsing.

## Ansible roles

Every role is opinionated and idempotent. Re-running `ansible-playbook
site.yml` on a healthy box should be a no-op. Where idempotency is hard
(cache busting, content downloads), the role delegates to a Python tool
rather than encoding it in YAML.

### `base`

- apt update / unattended-upgrades configured but disabled by default
- ssh hardening: key-only, no root login, separate `treehouse` user
- nftables base: deny-by-default in/out on the kids segment, allow
  established/related, allow LAN-internal
- chrony: configure as stratum-10 local source (no upstream)

### `network`

- dnsmasq: DHCP scope on the kids segment, wildcard `.kids` to host IP,
  TFTP off, DNSSEC off (no upstream to validate against)
- bridge configuration (`br-kids` static IP)
- nftables drop-all-forward rule for traffic from kids segment to other
  interfaces

### `proxy`

- nginx install (system apt package)
- `treehouse.conf` rendered from a Jinja template that knows the service set
  - per-service vhost (`wikipedia.kids`, `khan.kids`, ...) plus the
    public-name aliases from `treehouse.yml` `hostnames:` table
  - broker-redirect rule (no `kidsession` → bounce to launcher)
  - admin vhost (`admin.kids`) with HTTP basic via `htpasswd`
- TLS termination is upstream (a separate reverse-proxy box at the
  household edge), not on the box itself. Inside the kids' segment
  it's HTTP only. If you want a self-signed CA in the future, that's
  a future addition; M1 deliberately doesn't ship one.

### `docker`

- docker-ce and compose plugin
- `treehouse` user added to `docker` group
- daemon config: explicit log driver, log rotation
- pre-fetch container images via `docker compose pull` so cold starts on
  the Pi are quick

### `treehouse-services`

- Render `compose.yml` from template (env-specific values: hostnames,
  data paths, model size, etc.)
- `docker compose up -d`
- Health check: poll each service's adapter `health()` until all green
  or timeout

### `content-bootstrap` (optional, first-run only)

- If a `seed/` directory is present in the inventory, copy its contents
  into `content/zims/` so the Pi can ship pre-loaded.
- Skips entirely on subsequent runs.

### `backup`

- restic install
- repository init (idempotent)
- systemd timer for nightly snapshot of `state/` and `config/`
- Documentation file `docs/RECOVERY.md` rendered with this box's
  specific repository URL and password reference.

## Docker Compose

`compose.yml` is the runtime manifest. It is rendered from a template
because some values differ by deployment (hostnames, RAM allocations,
which model Ollama serves), but the structure is stable.

Conventions:

- Bind mounts everywhere — never named volumes. Files visible on the
  host at `/srv/treehouse/state/<service>/`. Easier to inspect, back
  up, and migrate.
- Per-service `restart: unless-stopped`.
- Read-only mounts where the service shouldn't be writing (kiwix on
  `content/zims:ro`, tileserver on `content/osm:ro`).
- Healthchecks defined for each service that has a meaningful one;
  Compose's `depends_on: condition: service_healthy` for ordering.
- Per-service log size cap (`logging.options.max-size: 10m`,
  `max-file: 3`).
- A single private network `treehouse-net` for inter-service traffic;
  the compose proxy (nginx) is the only thing exposed on the host
  network. The host nginx forwards to it on `127.0.0.1:18080`.

A representative excerpt:

```yaml
services:
  kiwix:
    image: ghcr.io/kiwix/kiwix-serve:latest
    restart: unless-stopped
    volumes:
      - /srv/treehouse/content/zims:/data:ro
    networks: [treehouse-net]
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://localhost:8080/catalog/v2/root"]
      interval: 30s
      timeout: 5s
      retries: 3

  kolibri:
    image: learningequality/kolibri:latest
    restart: unless-stopped
    volumes:
      - /srv/treehouse/state/kolibri:/root/.kolibri
      - /srv/treehouse/content/kolibri-content:/root/.kolibri/content
    networks: [treehouse-net]

  # ... etc
```

## Storage layout

The single most important physical property of the box. Documented in
detail in `architecture.md`; recapped here for completeness.

```
/srv/treehouse/
├── config/                   # in git, ~MB
│   ├── compose.yml           (rendered)
│   ├── nginx.conf            (mounted into compose proxy)
│   ├── dnsmasq/kids.conf
│   ├── nftables/kids.nft
│   └── per-service/...
├── state/                    # BACK UP — small, precious
│   ├── postgres-peertube/
│   ├── postgres-synapse/
│   ├── mongo-sugarizer/
│   ├── kolibri/
│   ├── synapse-media/
│   ├── calibre/
│   ├── launcher/             (credentials cache, sessions)
│   ├── searchd/              (history, cache)
│   └── aigateway/            (transcripts)
├── content/                  # huge, replaceable
│   ├── zims/                 (mounted :ro by kiwix)
│   ├── osm/                  (mounted :ro by tileserver)
│   ├── peertube-videos/
│   ├── peertube-thumbnails/
│   ├── kolibri-content/
│   ├── books/                (Calibre's actual files)
│   └── ai-models/            (Ollama model store)
├── launcher/                 # built static assets
│   ├── index.html
│   ├── tiles/
│   └── assets/
└── logs/                     # rotated
```

The system disk holds `config/`, `state/`, `launcher/`, `logs/` —
typically <50 GB total. The content disk holds `content/` — sized for
the manifest, typically 500 GB–2 TB.

## Sizing

| Resource | libvirt VM dev | Pi production |
|---|---|---|
| CPU | 4 vCPU | Pi 5 (4 cores) |
| RAM | 8 GB | 8 GB (Pi 5 max) |
| System disk | 40 GB | 32 GB SD or partition on NVMe |
| Content disk | 1 TB | 1–2 TB NVMe via PCIe HAT |
| Network | bridged adapter | Onboard WiFi or external AP |

The bottleneck is RAM, not CPU. Postgres × 2, MongoDB, Synapse, Ollama,
PeerTube, and Kolibri together comfortably fit in 8 GB but leave little
slack. If a future model requires more RAM (e.g. a 7B LLM), the Pi 5's
8 GB ceiling becomes the limiting factor and the architecture argues
for moving the AI off-box (a small Mac mini, or a dedicated mini-PC).

Disk is dominated by content. Realistic numbers:

| Bucket | Comfortable plan |
|---|---|
| ZIMs | 250 GB |
| OSM tiles (regional) | 10 GB |
| OSM tiles (planet) | 80 GB |
| Kolibri channels | 50–200 GB |
| PeerTube videos | 100–500 GB |
| Calibre library | 5–20 GB |
| Total | ~500 GB to 1 TB |

## Pi migration path

The migration from VM to Pi is the moment the project leaves the
laptop. Once the Compose stack and Ansible roles are stable on the VM,
the Pi adds three things:

1. A different inventory (`ansible/inventory/pi`).
2. A baked image (Packer + pi-gen) that ships with Docker, Compose, the
   `treehouse` user, and the playbook pre-cloned.
3. Hardware-specific tweaks: hostapd if running WiFi onboard, NVMe
   tuning (turning off SD card writes for `state/`), watchdog enabled.

The migration runbook (in `docs/operations.md`) is:

1. Snapshot VM state with restic to external disk.
2. Flash Pi image, first-boot to capture initial config (SSH key
   injection, hostname).
3. Restore state from restic onto Pi.
4. Run Ansible against pi inventory; bring up Compose.
5. Health-check every service.
6. Switch DNS at the kids' AP from VM IP to Pi IP.
7. Decommission VM after a week of stable operation.

## What the Makefile gives you

```
make up              # libvirt VM up + cloud-init seed
make down            # tear down the VM (base image kept for next up)
make provision       # rerun ansible against existing VM
make compose         # docker compose up -d on the box
make logs            # tail aggregated container logs
make health          # poll every adapter's health(), color-coded
make backup          # one-shot restic snapshot
make restore SNAP=…  # restore from a specific snapshot
make test-unit       # vitest + pytest fast suite
make test-e2e        # bring up compose.test.yml + Playwright
make manifest-plan   # updater --dry-run
make manifest-apply  # updater
make provision-kids  # provisioner
```

The Makefile is the operator UX. If a recurring operation isn't here,
it should be.
