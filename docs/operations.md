# Operations

How Treehouse is run day-to-day: backups, observability, maintenance,
and recovery from the various ways things break.

The operator is one person (Mick). The system is expected to run for
years with minimal attention. Operational design decisions favour
"boring and infrequent" over "elegant and continuous."

## Day-0 setup walkthrough

Going from a fresh Ubuntu/Debian laptop to a working development VM.
The dance has more steps than you'd expect; most of them are one-time
permission/group plumbing that bites on first contact.

### 1. One-time host bootstrap

```bash
make bootstrap-host
```

That's a single make target that runs steps 1–3 below. Idempotent;
safe to re-run on a partially-set-up box. After it finishes, **start a
new shell** so the new group memberships apply.

The same steps spelled out, if you'd rather run them by hand:

#### 1a. Host packages

```bash
sudo apt install -y \
  libvirt-daemon-system libvirt-clients qemu-system-x86 qemu-utils \
  virtinst genisoimage \
  ansible \
  docker.io docker-compose-v2
```

#### 1b. Group memberships (one-time)

```bash
sudo usermod -aG libvirt,kvm,docker $(whoami)
# Log out + back in, OR open a new shell, OR `newgrp libvirt`.
# Easiest: just reboot or restart your terminal session.
```

The catch: an existing shell does NOT see the new groups. `id -nG` in
the current shell will lie about your effective groups until you start
a fresh login. If you're driving this from a tmux session that predates
the usermod, every libvirt/kvm command needs an explicit
`sg libvirt -c '...'` wrapper. Re-launch the session to make it stop.

#### 1c. VM storage directory (one-time)

VM disks can't live under `/home/$USER/` because `libvirt-qemu`
(the QEMU runtime user) can't traverse mode-`750` home directories.
Put them where libvirt naturally expects, owned by you:

```bash
sudo mkdir -p /var/lib/libvirt/images/treehouse
sudo chown $USER: /var/lib/libvirt/images/treehouse
```

The default location is overridable via `TREEHOUSE_STORAGE_DIR`. Once
created, `bin/up.sh` writes the base image, per-VM disks, seed ISO, and
file-backed serial log here.

### 2. SSH key (auto-detected)

`bin/up.sh` looks for `~/.ssh/id_ed25519.pub` (then `id_rsa.pub`,
`id_ecdsa.pub`) and bakes it into the cloud-init seed. If none exists
it generates an ed25519 keypair for you.

### 3. Bring the VM up

```bash
make up
```

What happens:

1. Defines + starts the libvirt `br-kids` bridge network if needed.
2. Downloads the Debian 12 generic-cloud image (~330 MB) on first run.
3. Creates a 40 GB system disk (qcow2 overlay on the base) and a 200 GB
   sparse content disk in `/var/lib/libvirt/images/treehouse/`.
4. Generates a cloud-init NoCloud seed ISO (attached as virtio-blk —
   the cloud kernel has no AHCI driver, so a SATA cdrom would be
   invisible to the guest).
5. `virt-install --import` boots the VM with two NICs: management on
   the libvirt `default` net (NAT, gets a DHCP lease around
   `192.168.122.x`), and isolated on `br-kids`.
6. Waits ~30 s for cloud-init to settle, then probes SSH.
7. Writes the dynamic IP into `ansible/inventory/libvirt`.

The serial console is captured to
`/var/lib/libvirt/images/treehouse/treehouse-console.log`, owned by
your user so you can `cat` it without sudo. If `make up` hangs or the
VM never gets an IP, that file is your first stop.

### 4. Provision

Wait until cloud-init has finished its first-boot apt refresh
(`ssh mick@<ip> 'cloud-init status'` → `done`). Then:

```bash
make provision
```

Runs the Ansible playbook against the inventory written by `make up`.
Roles: `base` → `network` → `proxy` → `docker` → `treehouse-services`
→ `backup`. Idempotent — re-running on a healthy box should report
`changed=0`.

The `network` role is the dangerous one: it installs nftables with a
default-drop input policy, and the SSH carve-out is keyed off the
detected management interface name. Interface names are discovered via
ansible facts (`ansible_default_ipv4.interface`), so any naming policy
works (`enpXsY`, `ethN`, etc.) — but if facts ever return something
unexpected, expect to lose SSH and recover from the file-backed serial
console.

### 5. Seed content

```bash
make seed-wikipedia
```

SSHes to the VM, curls the top-100-articles Wikipedia ZIM (~50 MB) into
`/srv/treehouse/content/zims/`, runs `kiwix-manage add` inside the
container as root (the bind-mounted `library.xml` is owned by the host
`treehouse` user, so the unprivileged container user can't write
otherwise), then `docker restart`s kiwix to pick up the new entry.

The `wikipedia_en_for_schools.zim` build is no longer published; the
`WIKIPEDIA_SEED_NAME` variable in the Makefile points at the closest
substitute. Bump the date as new builds land at
<https://download.kiwix.org/zim/wikipedia/>.

### 6. Health checks

```bash
make verify-isolation   # boots a throwaway tablet VM on br-kids, runs the probe via cloud-init
make restore-drill      # restic restore of the latest snapshot
make test-live          # pytest against TREEHOUSE_HOST=10.10.10.1
```

## Daily loop

Most iteration happens via `make dev-up` / `make dev-down` (compose on
the laptop, no VM). The VM gates exist for milestone checkpoints, not
per-edit cycles.

When you do touch the VM:

| Want to | Run |
|---|---|
| Re-apply ansible after editing a role | `make provision` |
| Restart just the containers | `ssh mick@<ip> 'sudo docker compose -f /srv/treehouse/compose.yml restart'` |
| Read the kernel/cloud-init log | `cat /var/lib/libvirt/images/treehouse/treehouse-console.log` |
| Read kiwix container logs | `ssh mick@<ip> 'sudo docker logs treehouse-kiwix'` |
| Throw it away and start over | `make down && make up && make provision && make seed-wikipedia` |

## Known paper-cuts (resolved in tree)

These were live bugs found while bringing M1 up the first time. The
fixes are in the tree, but if you're chasing similar symptoms in a
fork, the patterns are worth knowing.

- **`virsh` defaults to `qemu:///session` for non-root users.** Bare
  `virsh` calls in scripts create per-user "phantom" networks that
  can't bind real bridges. `bin/up.sh` exports
  `LIBVIRT_DEFAULT_URI=qemu:///system` to force the system scope.
- **`set -o pipefail` + `grep -q` false negatives.** A pipeline like
  `cmd | grep -q foo` can return non-zero even when `foo` matched —
  `grep -q` exits early and the upstream command dies with SIGPIPE.
  `bin/up.sh` uses `grep ... >/dev/null` (no `-q`) where it matters.
- **Cloud kernel lacks AHCI.** The Debian generic-cloud kernel ships
  only virtio block drivers. Attach the cloud-init seed ISO as
  virtio-blk, not as a SATA cdrom, or the guest doesn't see it and
  cloud-init silently no-ops.
- **`<serial type='file'>` ignores dynamic_ownership.** Libvirt creates
  the file as `root:root 600`. Solved with an explicit DAC seclabel in
  the virt-install command (`source.seclabel.label=+UID:+GID`), which
  makes libvirt chown to your user on VM start.
- **`docker kill -s HUP` doesn't reach kiwix-serve.** Our kiwix
  entrypoint wraps the binary in a shell so an empty library doesn't
  crash-loop the container. PID-1 shells ignore HUP unless trapped, so
  the Makefile uses `docker restart` for library reloads.
- **Compose relative paths resolve against the compose file's dir.**
  `./content/zims:/data` in `/srv/treehouse/compose.yml` mounts
  `/srv/treehouse/content/zims`. Putting the compose file under
  `/srv/treehouse/config/` would silently mis-resolve the bind-mounts.
  The role drops it at `/srv/treehouse/` to mirror the repo layout.
- **systemctl restart systemd-networkd ≠ networkctl reload.** A
  restart doesn't always re-evaluate `/etc/systemd/network/*.network`
  against existing links. The handler uses `networkctl reload` to
  reliably pick up new interface configs.

## Backup

### What gets backed up

`/srv/treehouse/state/` and `/srv/treehouse/config/` only.

`content/` is excluded — it's huge, and re-fetchable from `manifest.yml`.

`logs/` is excluded — historical logs aren't worth restoring.

The launcher's `credentials.sqlite` is included, even though it can
be rebuilt — restoring it is faster than re-running the rotate flow,
which involves the box being healthy first.

### How

restic snapshots, daily, to an external USB drive (or a NAS). The
restic repository password is stored in `state/launcher/secrets.yml`
and printed verbatim into `docs/RECOVERY.md` during Ansible
provisioning so it's accessible from the printed runbook even if the
box is dead.

```ini
# /etc/systemd/system/treehouse-backup.timer
[Timer]
OnCalendar=daily
RandomizedDelaySec=15m
Persistent=true
```

```ini
# /etc/systemd/system/treehouse-backup.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/treehouse-backup.sh
```

Snapshot retention: 7 daily, 4 weekly, 12 monthly, 3 yearly. Total
storage on the backup drive is small (each snapshot is delta-encoded;
total state is <10 GB).

### Recovery drill

The single most important operational habit: **once a quarter,
restore from backup to a fresh VM and verify each service comes up
with intact data.**

```
# In a separate workspace, not the production box
make restore-drill SNAPSHOT=latest

# This:
#   1. Spins up a clean Vagrant VM
#   2. Runs Ansible to install services
#   3. restic restore /srv/treehouse/state/ from the chosen snapshot
#   4. Brings up Compose
#   5. Asserts: each adapter health() is OK
#   6. Asserts: at least one user from kids.yml is present in each
#      backend with intact data (Sugarizer journal entries, Kolibri
#      progress, etc.)
#   7. Tears down the drill VM

# Result is logged; emails / writes to docs/last-recovery-drill.md
```

A backup that hasn't been restored isn't a backup. A drill in the
calendar (recurring quarterly event) is the only reliable enforcer.

## Observability

The whole observability surface lives at `admin.kids` (HTTP basic
authed). Three pages:

### `admin.kids/health`

A dashboard rendered by the launcher's admin endpoint, polling each
adapter's `health()` every 30 seconds:

```
Treehouse — system health (last update 14:32:11)

✓  kiwix         200 OK · 4 ZIMs loaded
✓  kolibri       200 OK · 2 channels · 142 GB used
✓  sugarizer     200 OK
✓  peertube      200 OK · 87 videos · transcoding queue: empty
✓  synapse       200 OK · 4 users · 12 rooms
✓  calibre-web   200 OK · 612 books
✓  tileserver    200 OK · 1 region (great-britain)
✓  ollama        200 OK · model loaded: llama3.2:3b-q4_K_M
✓  searchd       200 OK · cache: 1247 entries
✓  aigateway     200 OK · transcripts today: 4

Disk:    312 GB / 1024 GB used   ░░░░██░░░░░░░░░░  30%
Memory:  4.2 GB / 8 GB used      ░░░░░░░░░░██████  52%
Uptime:  17 days
Last update run:    2026-05-04 (read report)
Last backup:        2026-05-04 03:14 (✓ ok, 9 GB total)
Last drill:         2026-02-03 (✓ ok)
```

### `admin.kids/updates`

The latest content-update report rendered inline (the markdown
written by `updater`), with links to prior reports.

### `admin.kids/transcripts/<kid_id>`

The AI gateway's transcript log, paginated. Filter by topic-gate
decision, by date range, by source mention.

### Logs

Beyond the admin pages, raw logs live in journald (per service)
and in `/srv/treehouse/logs/`. `make logs` tails all container
logs. Used for ad-hoc debugging only — the dashboard is the
expected operator surface.

## Software updates

The host is offline. Container images and OS packages do not get
updates passively. Two update vectors exist:

### Container images

`docker compose pull` during a maintenance window. Tags pinned in
`compose.yml` to specific versions; bumps are explicit edits to
`compose.yml`, committed to git, and accompanied by manual testing
on the VM before deploying to the Pi.

A monthly task: review upstream releases for the dozen images we
run, decide which to bump, test on VM, deploy.

### OS packages

Same pattern: `apt update && apt upgrade` during a maintenance
window. Run via Ansible against the inventory. Reboot if the kernel
or anything in PID 1 changed.

### Maintenance windows

A "maintenance window" is operationally:

```
make unseal           # bring up management interface
make backup           # snapshot before changes
# ... do work ...
make health           # verify everything still healthy
make seal             # bring management interface down
```

`unseal` raises the management interface and adds a default route
for the host (not the kids segment). Internet reachable from the
host. **The kids' segment remains isolated** because the nftables
forward `drop` rule still applies, and the kids' segment still has
no default route in its DHCP config.

`seal` reverses: brings management down, removes the host's default
route, leaves only the kids segment. The host can no longer reach
anything; only inbound traffic from the kids' segment is accepted.

After each `seal`, run a verification ritual:

```
make verify-isolation
# Expected output:
#   ✓ no default route on kids segment
#   ✓ nftables forward policy drop, kids→* drop rule present
#   ✓ management interface down
#   ✓ no DNS resolution to public IPs from a test client
```

Don't trust "I remember running unseal" — let the verification
script tell you the state is clean.

## NTP without internet

No upstream. Two valid setups:

### Local stratum-10 chrony (default)

The host advertises itself as a clock source. Time is whatever the
RTC says at boot, slewed slowly. Drift over months is tens of
seconds — fine for everything kid-facing.

```
# /etc/chrony/chrony.conf
local stratum 10
allow 10.10.10.0/24
makestep 1.0 3
rtcsync
```

### RTC battery on the Pi

Pi 5 has a CR2032 battery socket. Without a battery, every reboot
resets to epoch and Postgres has indigestion. **Always install
the battery.** Documented in the Phase 9 migration runbook.

### GPS time source (optional)

Overkill for a family deployment. A $40 USB GPS dongle gives you
stratum-1 if you ever need it. Mentioned for completeness.

## Power events

### Unclean shutdown

Postgres and MongoDB do not love this. A few mitigations:

1. **UPS:** a $40 mini-UPS on the host. Buys 5–15 minutes; long
   enough to ride out brief outages and to cleanly shut down on
   sustained ones. Listed in the Pi 9 migration BOM.
2. **systemd shutdown hook:** brief grace period for Compose to
   `docker compose stop` cleanly. Default systemd `ShutdownTimeout`
   is 90 seconds, plenty.
3. **Postgres autovacuum + WAL fsync:** default Postgres config is
   already crash-safe. Don't disable `fsync` "for performance" —
   the box doesn't need that performance.

If a Postgres did corrupt: `state/postgres-*` is in restic; restore
from yesterday's snapshot, lose at most a day of state.

### Reboot

Unattended-reboot-safe. Compose comes up automatically on boot;
each service's `restart: unless-stopped` brings it back. No
post-boot ritual required.

## Mischief recovery

A kid figures out something they shouldn't, breaks something, needs
a reset. The recovery is per-service:

| Symptom | Recovery |
|---|---|
| Kid deleted their Sugarizer journal entry | restic restore `state/mongo-sugarizer/` from yesterday |
| Kid wrote inappropriate content in a Matrix room | admin redacts the message via Synapse admin API; logs reviewed |
| Kid changed their PIN to lock themselves out | edit `kids.yml`, re-provision |
| Kid uploaded weird content to PeerTube | admin deletes via PeerTube admin UI |
| Kid found a Caddy admin path | check Caddy config — should not have been exposed; tighten |

A general-purpose nuke option for a single kid: `make nuke-kid
KID=alice`. This:

1. Disables Alice in `kids.yml` (sets `permissions.*: false`)
2. Re-provisions, which deactivates her in each backend
3. Snapshots her state for review
4. Awaits explicit re-enable

Heavy. Rarely needed. Documented for completeness.

## Disk full

### What happens

Postgres becomes unhappy first (cannot write WAL); kiwix-serve
keeps serving but nothing new can be downloaded; Synapse stops
accepting messages.

### Defenses

1. **`policies.disk_reserve` in `manifest.yml`** — updater preflight
   refuses to start a download that would breach the reserve.
2. **`admin.kids/health` shows disk gauge** — visible at every
   glance.
3. **Daily backup includes a disk usage snapshot in its report**
   — slow growth visible in retrospective.

### Recovery

In order of decreasing reversibility:

1. **Trim PeerTube videos** — delete least-watched. PeerTube admin
   UI or `peertube import-videos --remove`. Re-importable from
   manifest.
2. **Drop OSM regions** that aren't actively used.
3. **Drop ZIMs** with `keep: 1` on the largest (Wikipedia full →
   Wikipedia for Schools, ~95 GB saved).
4. **Add storage** — a second USB drive mounted at
   `content/extra/`, services configured to read from both.

## Service crash

Each container has `restart: unless-stopped`. If a service crashes,
Docker restarts it within seconds. Persistent crashes (restart loop)
show up in the dashboard as red.

For a true wedge:

```
make logs SERVICE=peertube  # see what happened
docker compose restart peertube
# if that doesn't help:
docker compose down peertube
# investigate state/postgres-peertube/, fix
docker compose up -d peertube
```

A service whose state is corrupt-and-can't-recover is a restic
restore candidate.

## Pi-specific operational notes

When the box is a Pi:

- **NVMe over USB3 vs PCIe HAT:** PCIe HAT is faster and more
  reliable. USB3 SSDs work but occasionally drop links under
  sustained write — annoying for content updates.
- **Cooling:** active cooling for the Pi 5 (the official cooler
  fan is fine). Without it, sustained content downloads
  thermally throttle.
- **PoE option:** a PoE HAT plus a PoE switch removes the wall
  wart from the equation. Helpful if the Pi lives in a placement
  where outlets are inconvenient.
- **SD card:** put nothing important on the SD card. Boot from
  NVMe directly (Pi 5 supports it). The SD card, if used, is
  for emergency recovery only.

## Quarterly checklist

Set a recurring calendar event:

- [ ] Run `make restore-drill`. Verify it passed. File the report.
- [ ] Review container image versions for upstream updates.
- [ ] Patch host: `make unseal && apt upgrade && reboot && make seal`.
- [ ] Spot-check the AI transcript log for unexpected patterns.
- [ ] Curation review: prune YouTube channels she's outgrown; add
      what's been showing up in search misses.
- [ ] Disk review: any service growing faster than expected?
- [ ] Update `kids.yml` if ages have ticked over an `age_band`.

## Annual checklist

- [ ] Replace Pi RTC battery (CR2032) preemptively.
- [ ] Replace UPS battery if it's the consumer type that degrades.
- [ ] Re-flash the Pi from a fresh image (not strictly needed but
      gives confidence the build process still works).
- [ ] Review `docs/` for staleness against current code.

## Open questions

- **Should there be off-site backup?** A second restic remote on a
  cloud bucket. Adds complexity (the management interface needs to
  be unsealed for restic to push); buys disaster resilience (house
  fire, drive failure on backup drive). Reasonable for v2;
  not blocker for v1.
- **Should the system page Mick on failures?** A push notification
  when an adapter goes red, when the update report shows errors.
  Requires upstream connectivity (or an out-of-band mechanism).
  Defer until the system is in production long enough to know what
  noisy-vs-useful looks like.
- **Should there be a "kid mode" for Mick's review of the box?**
  i.e. log into the launcher as Alice to see exactly what she sees.
  Achievable today via PIN; adding it as a first-class admin feature
  ("view as <kid>") is a small enhancement.
