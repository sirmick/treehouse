# Make commands

The `Makefile` is the operator UX. If a recurring operation isn't here,
it should be. Run `make help` for the auto-generated list; this doc is
the longer reference.

Commands are grouped into three tiers — daily dev, VM gates, tests —
plus utilities.

## Daily dev (compose on the laptop, no VM)

The compose stack is the primary iteration surface. nginx + kiwix
in containers; tests hit `127.0.0.1:18080` with explicit `Host:`
headers.

| Target | What it does | When to run |
|---|---|---|
| `make dev-up` | `docker compose up -d` for nginx + kiwix on `127.0.0.1:18080`. Pulls images on first run. | First action after cloning, or after `make dev-down` |
| `make dev-down` | `docker compose down` — stops both containers, removes the network | Done iterating for the day |
| `make dev-restart` | Restart both containers in place (config reload) | After editing `nginx/nginx.conf` or `compose.yml` |
| `make dev-logs` | `docker compose logs -f --tail=100` — live-tail both | Something's not working, want to see why |
| `make dev-status` | `docker compose ps` — what's running | Quick sanity check |

## VM (libvirt) — milestone gates

Real dnsmasq, real nftables, real Ansible playbook. Run on
commit-checkpoint cadence, not per-iteration.

| Target | What it does | When to run |
|---|---|---|
| `make up` | Runs `bin/up.sh` — defines `br-kids` libvirt network if missing, downloads Debian 12 cloud image (~600 MB, cached), creates the treehouse VM via `virt-install` with cloud-init, waits for SSH, writes `ansible/inventory/libvirt` with the assigned IP | First M1 bring-up, or after `make down` |
| `make down` | `bin/down.sh` — `virsh destroy` + `undefine` + remove disks | Tearing the VM apart |
| `make provision` | `ansible-playbook -i inventory/libvirt site.yml` — runs the six roles in order: base, network, proxy, docker, treehouse-services, backup | After `make up`, or after editing any Ansible role |
| `make ssh-treehouse` | Reads the IP from `inventory/libvirt`, `ssh mick@<ip>` | Poking around inside the VM |

## Isolation gate

The single most important property milestone 1 validates: a device
on the kids' segment cannot reach the internet.

| Target | What it does | When to run |
|---|---|---|
| `make verify-isolation` | spins a throwaway "tablet" VM with a single NIC on `br-kids` (via `bin/verify-isolation-via-tablet.sh`), cloud-init runs `bin/verify-isolation` inside it, output captured via file-backed serial, VM destroyed on exit. `network.mode: isolated` only — refuses in `lan` mode. | Milestone 1 close-out — the actual gate |

The probe script asserts:
- No default route on the tablet
- `home.kids` resolves to 10.10.10.1
- Unknown hostnames sinkhole to 10.10.10.1
- TCP to `1.1.1.1:53/80/443` is unreachable
- HTTP to `home.kids` and `wikipedia.kids` returns 200

## Content seeding

Until Phase 3's updater lands, ZIM placement is manual.

| Target | What it does | When to run |
|---|---|---|
| `make seed-wikipedia` | reads the catalog name from `manifest.yml` (zims[0]), pairs it with `WIKIPEDIA_SEED_DATE` in the Makefile, ssh's into the VM, curls the ZIM (~50 MB for `wikipedia_en_100_maxi`) into `/srv/treehouse/content/zims/`, calls `kiwix-manage add`, then `docker restart`s kiwix to pick it up | Once after `make provision`, until Phase 3's updater replaces it |

## Backup

| Target | What it does | When to run |
|---|---|---|
| `make backup` | `ssh ... systemctl start treehouse-backup.service` — fires the one-shot restic snapshot manually instead of waiting for the daily timer | Before risky changes, or to ensure recent state before a drill |
| `make restore-drill` | `ssh ... restic restore latest --target /tmp/restore-drill && ls` — proves the backup is restorable. Lists what came back so you can eyeball it. | Quarterly per `operations.md`, or to validate after first ever `make backup` |

## Health smoke test

| Target | What it does | When to run |
|---|---|---|
| `make health` | Three `curl -sI` calls against `10.10.10.1` with `Host: home.kids`, `wikipedia.kids`, `youtube.com`. Prints the status line of each. | Smoke test after `make provision` or `make seed-wikipedia` |

## Testing

| Target | What it does | When to run |
|---|---|---|
| `make test` | `test-schemas` + `test-compose` — the daily-dev gate | Before every commit |
| `make test-schemas` | `pytest schemas/ -v` — 6 pydantic round-trip tests, no infra needed | Edited `schemas/*.py` |
| `make test-compose` | `pytest tests/test_compose.py tests/test_compose_proxy.py tests/test_adapters.py tests/test_provisioner.py -v` — fixture brings up `compose.test.yml`, runs all the container-shaped tests, tears down. ~10s. | Edited `compose.yml`, `nginx/`, `treehouse/`, or any test |
| `make test-live` | `TREEHOUSE_HOST=10.10.10.1 pytest tests/test_live.py -v` — same shape as the proxy tests but against the VM at 10.10.10.1. Skips silently if the VM isn't up. | After `make up && make provision` |
| `make test-all` | Schemas + compose + live | M1 close-out |
| `make test-deps` | Creates `.venv`, installs `tests/requirements.txt`. Idempotent. | Once after `git clone`, or after `make clean` |

## Utilities

| Target | What it does | When to run |
|---|---|---|
| `make toolchain` | Echoes the apt install command for the host packages | New machine, or you forgot what to install |
| `make help` | Greps the Makefile for `## ` comments, prints them aligned | "What targets exist?" |
| `make clean` | Removes `.cache`, `.venv`, ansible cache, pytest caches, `__pycache__` dirs | Resetting from clean state |

## Mental model — which to use when

| Situation | Run |
|---|---|
| Code change in Python / nginx / compose | `make test` (fast feedback, no VM) |
| Want to see the site in your browser | `make dev-up`, then `127.0.0.1:18080` with `Host:` headers (or with the systemd-resolved rule from `deployment.md`) |
| Editing Ansible roles | `make provision` against an already-up VM, or `make up && make provision` from cold |
| Closing out milestone 1 | `make up && make provision && make seed-wikipedia && make verify-isolation && make restore-drill && make test-live` |
| Done for the day | `make dev-down` (always cheap), `make down` (only if you want to free the ~50 GB the VM uses) |

## Adding a new target

The Makefile is the operator UX, so additions should follow the
existing pattern:

```makefile
.PHONY: my-target
my-target:                ## Short description that appears in `make help`
	command-here
```

The `## Short description` after the colon is what `make help`
extracts. Aim for two-thirds of one line on a 100-column terminal.

If a target's behaviour is non-obvious, also document it in this
file under the right tier.
