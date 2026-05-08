.PHONY: help \
        bootstrap-host \
        launcher-deps launcher-config launcher-build launcher-dev \
        up down wipe-content provision shell health \
        verify-isolation seed ingest backup restore-drill \
        test test-schemas test-compose test-live test-all test-deps \
        toolchain clean

# ============================================================================
# Two-tier dev model:
#
#   dev-*    — docker compose on the laptop. Daily iteration: caddy +
#              kiwix in containers; tests hit 127.0.0.1:18080. No VM
#              touched.
#
#   up / *   — libvirt VM (treehouse). Milestone gates: real dnsmasq,
#              real nftables, real network isolation. Run on commit
#              checkpoints, not per-iteration.
#
# ============================================================================

ANSIBLE_DIR := ansible
INVENTORY   := $(ANSIBLE_DIR)/inventory/libvirt
PLAYBOOK    := $(ANSIBLE_DIR)/site.yml
COMPOSE     := docker compose

# ZIM versioning lives in bin/ingest-all.sh and bin/seed-vm.sh (the
# (kiwix-dir, catalog-name, version-date) tuples). Until the Phase-3
# updater lands those scripts are the bridge between manifest.yml's
# catalog names and concrete download URLs.

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | awk -F: '{ printf "  %-22s %s\n", $$1, $$NF }'

# ----- launcher (SvelteKit static frontend) --------------------------------
launcher-deps: launcher-config  ## npm install in launcher/ (run once after a fresh clone)
	cd launcher && npm install

launcher-config:                ## Generate launcher/src/lib/config.ts from treehouse.yml
	bin/launcher-config-gen

launcher-build: launcher-config ## Build static launcher assets into launcher/build/
	cd launcher && npm run build

launcher-dev: launcher-config   ## Vite dev server for the launcher (hot reload, port 5173)
	cd launcher && npm run dev

# Note: there's no `make dev-up` for laptop-side compose anymore. The
# whole-stack test path runs through the VM via `make up && make
# provision`; the launcher dev surface is `make launcher-dev` (vite).

# ----- VM (libvirt) — milestone gates --------------------------------------
up:                        ## Bring up the treehouse VM via libvirt
	bin/up.sh

down:                      ## Tear down the treehouse VM (preserves content disk)
	bin/down.sh

wipe-content:              ## Delete the content qcow2 (next make up re-creates it blank)
	@CD="$${TREEHOUSE_STORAGE_DIR:-/var/lib/libvirt/images/treehouse}/treehouse-content.qcow2"; \
	if [ -f "$$CD" ]; then \
	  echo "removing $$CD"; rm -f "$$CD"; \
	else \
	  echo "no content disk at $$CD (already wiped)"; \
	fi

provision:                 ## ansible-playbook against the VM via qemu-guest-agent
	cd $(ANSIBLE_DIR) && sg libvirt -c 'ansible-playbook -i inventory/libvirt-qemu site.yml'

shell:                     ## Open an interactive shell on the VM (virsh console; Ctrl-] to exit)
	sg libvirt -c 'LIBVIRT_DEFAULT_URI=qemu:///system virsh console treehouse'

# ----- isolation gate (runs probes from a throwaway VM on br-kids) ---------
# verify-isolation only makes sense in isolated mode — the gate proves
# that a device on br-kids can't reach the internet, but in lan mode
# the VM IS on the LAN by design.
verify-isolation:          ## Spin a throwaway tablet VM on br-kids and run the probe inside it
	@mode=$$(bin/cfg network.mode); \
	if [ "$$mode" != "isolated" ]; then \
	  echo "verify-isolation requires network.mode: isolated (current: $$mode)" >&2; \
	  echo "the gate proves br-kids can't reach upstream; lan mode is on the LAN by design." >&2; \
	  exit 1; \
	fi
	bin/verify-isolation-via-tablet.sh

# ----- content (runs on the VM via qemu-ga) --------------------------------
seed:                      ## Download every ZIM in group_vars (zims), kiwix-manage add, restart kiwix
	cd $(ANSIBLE_DIR) && sg libvirt -c 'ansible-playbook -i inventory/libvirt-qemu seed.yml'

ingest:                    ## Index every ZIM into MeiliSearch (VM-side; deps installed on first run)
	cd $(ANSIBLE_DIR) && sg libvirt -c 'ansible-playbook -i inventory/libvirt-qemu ingest.yml'

# ----- backup --------------------------------------------------------------
backup:                    ## Trigger a one-shot restic snapshot now
	cd $(ANSIBLE_DIR) && sg libvirt -c 'ansible -i inventory/libvirt-qemu treehouse -b -m systemd -a "name=treehouse-backup.service state=started"'

restore-drill:             ## Take a fresh snapshot, then restore it to /tmp on the VM
	cd $(ANSIBLE_DIR) && sg libvirt -c 'ansible -i inventory/libvirt-qemu treehouse -b -m shell -a "set -e; systemctl start treehouse-backup.service; rm -rf /tmp/restore-drill; restic --repo /srv/treehouse/state/restic-repo --password-file /etc/treehouse/restic.password restore latest --target /tmp/restore-drill; ls /tmp/restore-drill/srv/treehouse/"'

# ----- health -------------------------------------------------------------
health:                    ## Smoke test against the VM
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	curl -sI -H 'Host: home.kids' http://10.10.10.1/ | head -1; \
	curl -sI -H 'Host: wikipedia.kids' http://10.10.10.1/ | head -1; \
	curl -sI -H 'Host: youtube.com' http://10.10.10.1/ | head -1

# ----- testing ------------------------------------------------------------
test:                      ## Schemas + compose + adapters + provisioner
	$(MAKE) test-schemas
	$(MAKE) test-compose

test-schemas:              ## Round-trip manifest/kids schemas
	.venv/bin/python -m pytest schemas/ -v

test-compose:              ## Spin up kiwix in compose, run kiwix + adapter + provisioner tests
	.venv/bin/python -m pytest tests/test_compose.py tests/test_adapters.py tests/test_provisioner.py -v

test-live:                 ## Hit the deployed VM via SSH local-forward to 10.10.10.1:80
	bin/test-live.sh

test-all:                  ## All suites (schemas + compose + live)
	$(MAKE) test-schemas
	$(MAKE) test-compose
	$(MAKE) test-live

test-deps:                 ## Install python test deps into a local .venv
	python3 -m venv .venv
	./.venv/bin/pip install -r tests/requirements.txt
	@echo "activate: source .venv/bin/activate"

# ----- one-time host setup -------------------------------------------------
bootstrap-host:            ## One-time host setup: apt deps, groups, libvirt storage dir
	@echo "==> Installing host packages (sudo prompt)"
	sudo apt install -y \
	  libvirt-daemon-system libvirt-clients qemu-system-x86 qemu-utils \
	  virtinst genisoimage \
	  ansible \
	  docker.io docker-compose-v2
	@echo
	@echo "==> Adding $$USER to libvirt, kvm, docker groups"
	sudo usermod -aG libvirt,kvm,docker $$USER
	@echo
	@echo "==> Creating libvirt storage at /var/lib/libvirt/images/treehouse"
	sudo mkdir -p /var/lib/libvirt/images/treehouse
	sudo chown $$USER: /var/lib/libvirt/images/treehouse
	@echo
	@echo "==> Done."
	@echo "    Open a NEW shell (or log out + back in) so the new groups apply,"
	@echo "    then run: make up"

toolchain:                 ## Print install command for missing host packages (use bootstrap-host instead)
	@echo "Prefer:  make bootstrap-host"
	@echo
	@echo "Manual:"
	@echo "  sudo apt install -y \\"
	@echo "    libvirt-daemon-system libvirt-clients qemu-system-x86 qemu-utils \\"
	@echo "    virtinst genisoimage \\"
	@echo "    ansible \\"
	@echo "    docker.io docker-compose-v2"
	@echo "  sudo usermod -aG libvirt,kvm,docker \$$(whoami)"
	@echo "  sudo mkdir -p /var/lib/libvirt/images/treehouse && sudo chown \$$USER: /var/lib/libvirt/images/treehouse"
	@echo "  newgrp libvirt   # or log out and back in"

clean:                     ## Remove .cache, .venv, ansible cache, .pytest_cache
	rm -rf .cache .venv ansible/.ansible .pytest_cache schemas/.pytest_cache tests/__pycache__ schemas/__pycache__ treehouse/__pycache__ treehouse/*/__pycache__
