# Deployment

How Treehouse is built and run, from `vagrant up` on a developer laptop
through to the Raspberry Pi that lives on the shelf in the family room.

The same Ansible roles and Compose stack target both. The only differences
between the VM and Pi deployments live in inventory variables (network
interface names, hostapd vs. external AP, image format) — never in
service configuration.

## Targets

| Target | Purpose | Hardware | When |
|---|---|---|---|
| `vagrant` | Iteration, CI, tests | libvirt or VirtualBox VM | All development |
| `pi` | Production family deployment | Raspberry Pi 5 + NVMe | After Phase 9 |
| `cloud` | Not supported | — | Explicitly out of scope |

The `cloud` row is here as a reminder: an earlier design considered a
public-internet demo face. Decided against. Treehouse is offline-only.
See `architecture.md` for the reasoning.

## Repository layout

```
treehouse/
├── Vagrantfile                     # libvirt by default, VBox fallback
├── Makefile                        # up / destroy / provision / test / deploy
├── compose.yml                     # all containers
├── compose.test.yml                # smaller content, deterministic fixtures
├── ansible/
│   ├── site.yml                    # top-level playbook
│   ├── inventory/
│   │   ├── vagrant
│   │   └── pi
│   ├── group_vars/
│   │   ├── all.yml
│   │   ├── vagrant.yml
│   │   └── pi.yml
│   ├── roles/
│   │   ├── base/                   # apt baseline, ssh, ufw
│   │   ├── network/                # dnsmasq, nftables, bridge
│   │   ├── proxy/                  # Caddy + per-service vhosts
│   │   ├── docker/                 # docker-ce + compose plugin
│   │   ├── treehouse-services/     # renders compose.yml, starts stack
│   │   ├── content-bootstrap/      # first-run ZIM seed (optional)
│   │   └── backup/                 # restic config + systemd timer
│   └── files/
│       └── caddy/Caddyfile.j2
├── treehouse/                      # Treehouse's own services
│   ├── launcher/                   # SvelteKit + FastAPI broker
│   ├── searchd/                    # FastAPI aggregator
│   ├── aigateway/                  # FastAPI in front of Ollama
│   ├── updater/                    # CLI: content sync
│   ├── provisioner/                # CLI: kid account sync
│   └── adapters/                   # shared adapter modules
├── packer/
│   ├── kids.pkr.hcl                # bake .qcow2 / .ova / Pi image
│   └── pi-gen.cfg                  # Pi-specific image config
├── manifest.yml                    # content (declarative)
├── kids.yml                        # kids (declarative)
├── docs/                           # design docs (this directory)
└── tests/
    ├── e2e/                        # Playwright
    ├── integration/                # pytest, hits real compose stack
    └── fixtures/
        ├── manifest.test.yml
        ├── kids.test.yml
        └── seed-zims/
```

The `treehouse/` Python packages and the SvelteKit launcher live in the
same repo so refactors that span the broker contract and the UI happen as
single PRs. Mono-repo for simplicity; not a polyglot zoo.

## Vagrant — the default development target

```ruby
# Vagrantfile (sketch)
Vagrant.configure("2") do |config|
  config.vm.box = "debian/bookworm64"
  config.vm.hostname = "treehouse"

  # Two networks:
  #   - management: NAT (lets vagrant ssh in, lets ansible apt-install)
  #   - kids: private bridge ("br-kids"), no upstream
  config.vm.network "private_network",
    libvirt__network_name: "br-kids",
    libvirt__forward_mode: "none",
    auto_config: false

  config.vm.provider "libvirt" do |v|
    v.memory = 8192
    v.cpus = 4
    v.storage :file, size: "1000G", type: "qcow2"
  end

  config.vm.synced_folder ".", "/vagrant", type: "rsync"

  config.vm.provision "ansible" do |a|
    a.playbook = "ansible/site.yml"
    a.inventory_path = "ansible/inventory/vagrant"
  end
end
```

Two networks: a NAT'd management interface that lets `vagrant ssh` work
and lets Ansible reach apt mirrors during initial provisioning, and a
private bridge `br-kids` that the kids' AP also connects to. The host
has a default route only via the management interface; the kids' bridge
explicitly does not. Once provisioned, the management interface can be
brought down for production runs (`make seal` flips it off).

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

- Caddy install (system package or static binary)
- `Caddyfile` rendered from a Jinja template that knows the service set
  - per-service vhost (`wikipedia.kids`, `khan.kids`, ...)
  - broker-redirect rule (no `kidsession` → bounce to launcher)
  - admin vhost (`admin.kids`) with HTTP basic
- Internal CA: optional; off by default (HTTP-only on the kids segment).
  When on, Caddy issues from its own CA and a one-shot script bundles the
  root cert for installation on devices.

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
  Caddy is the only thing exposed on the host network.

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
│   ├── caddy/Caddyfile       (rendered)
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

| Resource | Vagrant dev | Pi production |
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
make up              # vagrant up + ansible provision
make destroy         # tear down, keep content/
make provision       # rerun ansible against existing VM
make seal            # disable management interface (production-only)
make unseal          # re-enable management interface (maintenance)
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
