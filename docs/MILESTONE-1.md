# Milestone 1 — bringing it up

The first runnable slice of Treehouse, with two distinct paths:

| Path | What runs | When | Speed |
|---|---|---|---|
| **Daily dev (compose)** | caddy + kiwix in containers on your laptop | Every code change | seconds |
| **VM gate (libvirt)** | full host stack: dnsmasq + nftables + caddy + docker | Commit checkpoints | minutes |

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
make dev-up           # caddy + kiwix in containers, ~5 sec
```

What's running: `treehouse-caddy` on `127.0.0.1:18080`, reverse-proxying
`treehouse-kiwix` on a private compose network. To browse:

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
schemas, adapters, kiwix, and Caddy host-routing.

## 3. The VM gate

When you want to validate the host-level architecture (dnsmasq
sinkhole, nftables forward-drop, Caddy on the host, the restic
backup, **real network isolation**):

```sh
make up               # ~5 min: virt-install + cloud-init + cloud image fetch
make provision        # ansible-playbook brings up dnsmasq, Caddy, Docker, kiwix
make seed-wikipedia   # downloads Wikipedia for Schools (~5 GB) into the VM
make verify-isolation # the actual milestone gate
make restore-drill    # second milestone gate: backup actually works
make test-live        # pytest against the VM at 10.10.10.1
```

What `make verify-isolation` does:

1. SSHes into the treehouse VM
2. Creates a `tablet` network namespace inside the VM
3. Attaches a macvlan child of `eth1` (the kids' segment NIC) to the namespace
4. The namespace gets DHCP from the VM's dnsmasq → no upstream route
5. Runs probes (1.1.1.1 unreachable, DNS sinkhole works, `home.kids`
   reachable) inside the namespace
6. Cleans up the namespace

This simulates a tablet plugged into the kids' WiFi: same network,
no other path.

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
