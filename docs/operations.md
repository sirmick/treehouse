# Operations

How Treehouse is run day-to-day: backups, observability, maintenance,
and recovery from the various ways things break.

The operator is one person (Mick). The system is expected to run for
years with minimal attention. Operational design decisions favour
"boring and infrequent" over "elegant and continuous."

## Day-0 setup walkthrough

Going from a fresh laptop to a working development VM:

```
# Prerequisites: git, vagrant, libvirt (or VirtualBox), ansible

git clone git@github.com:sirmick/treehouse.git
cd treehouse

# Initial config
cp config/secrets.example.yml config/secrets.yml
$EDITOR config/secrets.yml      # set restic password, admin password
cp manifest.example.yml manifest.yml
cp kids.example.yml kids.yml

# Bring up the VM
make up                         # vagrant up + ansible provision
                                # ~5-10 minutes first time

# First-run content (small, just to validate)
make manifest-apply             # downloads any ZIMs in manifest.yml

# First kid
make provision-kids             # provisions accounts in each backend

# Sanity check
make health                     # all adapters green?

# Add the kids' AP to the LAN bridge (or use the VM's bridge from a tablet)
# Connect tablet to "treehouse" SSID
# Browse to home.kids
```

The first run is the slowest because container images download and
the first ZIM is fetched. Subsequent `make up`s on the same machine
take seconds.

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
