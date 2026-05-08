# Milestone 1 — bringing it up

The first runnable slice of Treehouse, with two distinct paths:

| Path | What runs | When | Speed |
|---|---|---|---|
| **Daily dev (compose)** | nginx + kiwix in containers on your laptop | Every code change | seconds |
| **VM gate (libvirt)** | full host stack: dnsmasq + nftables + nginx + docker | Commit checkpoints | minutes |

The compose stack is the daily iteration surface. The VM is for the
real isolation gate — the property that "a device on the kids'
segment cannot reach the internet" only means something on a real
separate-network setup, which the VM provides. Most days you'll
only run compose.

## 0. Toolchain on the host (one time)

```sh
sudo apt install -y \
  libvirt-daemon-system libvirt-clients qemu-system-x86 qemu-utils \
  virtinst genisoimage \
  ansible \
  docker.io docker-compose-v2

sudo usermod -aG libvirt,kvm,docker "$(whoami)"
newgrp libvirt   # or log out and back in
```

`make toolchain` prints these commands as a reminder.

Verify:

```sh
virsh list --all      # should not error; output may be empty
docker compose version
ansible --version
```

Then the python venv for tests:

```sh
make test-deps        # creates .venv and installs requirements
```

## 1. Daily dev: compose stack on the laptop

```sh
make dev-up           # nginx + kiwix in containers, ~5 sec
```

What's running: `treehouse-proxy` (nginx) on `127.0.0.1:18080`,
reverse-proxying `treehouse-kiwix` on a private compose network. To
browse:

```sh
curl -H 'Host: home.kids' http://127.0.0.1:18080/
curl -H 'Host: wikipedia.kids' http://127.0.0.1:18080/catalog/v2/entries
```

Or browser-flavoured: configure the systemd-resolved rule from
`deployment.md § Development DNS` and `home.kids` works in the URL bar.
But for daily dev the explicit `Host:` header is faster.

```sh
make dev-logs         # tail container logs
make dev-restart      # bounce the stack
make dev-down         # stop everything
```

## 2. Tests

```sh
make test             # schemas + compose suites (~10s)
make test-live        # against the VM (after `make up`)
make test-all         # everything
```

The compose tests bring up `compose.test.yml` (port 18181, scoped to
`treehouse-test` so it doesn't collide with `make dev-up`), assert
through requests, tear down. Same fixture infrastructure for
schemas, adapters, kiwix, and nginx host-routing.

## 3. The VM gate

When you want to validate the host-level architecture (dnsmasq
sinkhole, nftables forward-drop, host nginx, the restic backup,
**real network isolation**):

```sh
make up               # ~5 min: virt-install + cloud-init + cloud image fetch
make provision        # ansible-playbook brings up dnsmasq, nginx, Docker, kiwix
make seed-wikipedia   # fetches the manifest.yml zims[0] (~50 MB top-100) into the VM
make verify-isolation # the actual milestone gate (network.mode: isolated only)
make restore-drill    # second milestone gate: backup → restore round-trip
make test-live        # pytest against the VM at 10.10.10.1
```

What `make verify-isolation` does:

1. Spins up a throwaway "tablet" VM with a single NIC on `br-kids`
   (no management path, no SSH). It's a real guest, not a netns —
   exercises the segment exactly the way a kid's tablet would.
2. cloud-init drops `bin/verify-isolation` into the tablet at first
   boot, runs it, and writes the output to a file-backed serial.
3. Probes inside the tablet: no default route, `home.kids` resolves
   to 10.10.10.1, `youtube.com` is sinkholed, 1.1.1.1:53/80/443
   unreachable, HTTP 200 from the launcher and Wikipedia vhosts.
4. Host-side wrapper polls the serial for a sentinel, prints results
   to your terminal, destroys the tablet VM. Console log preserved
   at `/var/lib/libvirt/images/treehouse/treehouse-tablet-console.log`.

The tablet only exists in `network.mode: isolated`. In `lan` mode
(see `treehouse.yml`) the gate is moot and the target refuses with a
clear message — the VM is on the LAN by design and the property
"can't reach upstream" no longer applies.

## 4. Tear down

```sh
make dev-down         # stops the laptop containers
make down             # destroys the VM (keeps base cloud image cached)
```

`make clean` additionally removes `.venv`, `.cache`, ansible cache,
pytest cache.

## What's NOT here yet

- The launcher (Phase 2). `home.kids` is just a static placeholder.
- Identity broker, kids.yml plumbing (Phase 2). Schemas + provisioner
  exist but the broker doesn't.
- The updater, manifest.yml plumbing (Phase 3). ZIM placement is manual.
- searchd, aigateway (Phases 5 and 8).
- Every other content service.

`schemas/` has the locked-in `manifest.py` and `kids.py`. Schema is
fixed; nothing reads them from yaml yet (provisioner reads kids.yml
already).

`treehouse/adapters/` and `treehouse/provisioner/` are Phase 2
groundwork. They run in the test suite but aren't wired into a
running launcher.

## Common ops cheat sheet

```sh
make help                  # list of targets
make dev-up / dev-down     # daily compose loop
make up / down             # VM lifecycle
make provision             # re-run ansible against VM
make health                # smoke test the VM
make ssh-treehouse         # ssh into the VM
make verify-isolation      # the gate
make backup                # snapshot now
make restore-drill         # prove backup works
make test                  # schemas + compose tests
make test-live             # vs deployed VM
make clean                 # nuke caches
```
