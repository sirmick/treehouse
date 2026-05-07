.PHONY: help \
        bootstrap-host \
        launcher-deps launcher-build launcher-dev \
        dev-up dev-down dev-restart dev-logs \
        up down provision health ssh-treehouse \
        verify-isolation seed-wikipedia backup restore-drill \
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
COMPOSE_DEV := $(COMPOSE) -p treehouse
# Kiwix's `wikipedia_en_for_schools.zim` is no longer published. The
# closest substitute for a small, milestone-friendly seed is the
# top-100 maxi (with images, ~50 MB). Bump the date as new builds land.
WIKIPEDIA_SEED_NAME := wikipedia_en_100_maxi_2026-04.zim
WIKIPEDIA_SEED_URL  := https://download.kiwix.org/zim/wikipedia/$(WIKIPEDIA_SEED_NAME)

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | awk -F: '{ printf "  %-22s %s\n", $$1, $$NF }'

# ----- launcher (SvelteKit static frontend) --------------------------------
launcher-deps:             ## npm install in launcher/ (run once after a fresh clone)
	cd launcher && npm install

launcher-build:            ## Build static launcher assets into launcher/build/
	cd launcher && npm run build

launcher-dev:              ## Vite dev server for the launcher (hot reload, port 5173)
	cd launcher && npm run dev

# ----- daily dev (compose, no VM) ------------------------------------------
dev-up: launcher-build     ## docker compose up -d (nginx + kiwix on the laptop)
	$(COMPOSE_DEV) up -d
	@echo
	@echo "  Proxy: http://127.0.0.1:18080"
	@echo "  Try:   curl -H 'Host: home.kids' http://127.0.0.1:18080/"

dev-down:                  ## docker compose down
	$(COMPOSE_DEV) down

dev-restart:               ## restart the dev stack
	$(COMPOSE_DEV) restart

dev-logs:                  ## tail dev compose logs
	$(COMPOSE_DEV) logs -f --tail=100

dev-status:                ## ps the dev stack
	$(COMPOSE_DEV) ps

# ----- VM (libvirt) — milestone gates --------------------------------------
up:                        ## Bring up the treehouse VM via libvirt
	bin/up.sh

down:                      ## Tear down the treehouse VM
	bin/down.sh

provision:                 ## ansible-playbook against the VM
	cd $(ANSIBLE_DIR) && ansible-playbook -i inventory/libvirt site.yml

ssh-treehouse:             ## SSH into the treehouse VM
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	  ssh mick@$$IP

# ----- isolation gate (runs probes from a throwaway VM on br-kids) ---------
verify-isolation:          ## Spin a throwaway tablet VM on br-kids and run the probe inside it
	bin/verify-isolation-via-tablet.sh

# ----- content -------------------------------------------------------------
seed-wikipedia:            ## Manual ZIM placement: Wikipedia top-100 (milestone seed)
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	ssh mick@$$IP "sudo curl -L -o /srv/treehouse/content/zims/$(WIKIPEDIA_SEED_NAME) $(WIKIPEDIA_SEED_URL) && \
	  sudo docker exec --user root treehouse-kiwix kiwix-manage /data/library.xml add /data/$(WIKIPEDIA_SEED_NAME) && \
	  sudo docker restart treehouse-kiwix"

# ----- backup --------------------------------------------------------------
backup:                    ## Trigger a one-shot restic snapshot now
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	ssh mick@$$IP 'sudo systemctl start treehouse-backup.service'

restore-drill:             ## Take a fresh snapshot, then restore it to /tmp on the VM
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	ssh mick@$$IP '\
	  sudo systemctl start treehouse-backup.service && \
	  sudo rm -rf /tmp/restore-drill && \
	  sudo restic --repo /srv/treehouse/restic-repo \
	    --password-file /etc/treehouse/restic.password \
	    restore latest --target /tmp/restore-drill && \
	  sudo ls /tmp/restore-drill/srv/treehouse/'

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

test-compose:              ## Compose stack: kiwix, proxy host-routing, sinkhole
	.venv/bin/python -m pytest tests/test_compose.py tests/test_compose_proxy.py tests/test_adapters.py tests/test_provisioner.py -v

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
