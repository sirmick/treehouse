.PHONY: help \
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
WIKIPEDIA_FOR_SCHOOLS_URL := https://download.kiwix.org/zim/wikipedia/wikipedia_en_for_schools.zim

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | awk -F: '{ printf "  %-22s %s\n", $$1, $$NF }'

# ----- daily dev (compose, no VM) ------------------------------------------
dev-up:                    ## docker compose up -d (caddy + kiwix on the laptop)
	$(COMPOSE_DEV) up -d
	@echo
	@echo "  Caddy: http://127.0.0.1:18080"
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

# ----- isolation gate (runs probes from inside a netns on the VM) ----------
verify-isolation:          ## Probe network isolation from a netns on the VM
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	scp bin/verify-isolation mick@$$IP:/tmp/verify-isolation; \
	ssh mick@$$IP 'sudo bash -s' < bin/run-isolation-via-netns.sh

# ----- content -------------------------------------------------------------
seed-wikipedia:            ## Manual ZIM placement: Wikipedia for Schools
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	ssh mick@$$IP "sudo curl -L -o /srv/treehouse/content/zims/wikipedia_en_for_schools.zim $(WIKIPEDIA_FOR_SCHOOLS_URL) && \
	  sudo docker exec treehouse-kiwix kiwix-manage /data/library.xml add /data/wikipedia_en_for_schools.zim && \
	  sudo docker kill -s HUP treehouse-kiwix"

# ----- backup --------------------------------------------------------------
backup:                    ## Trigger a one-shot restic snapshot now
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	ssh mick@$$IP 'sudo systemctl start treehouse-backup.service'

restore-drill:             ## Restore latest snapshot to a fresh /tmp dir on the VM
	@IP=$$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' $(INVENTORY)); \
	ssh mick@$$IP '\
	  sudo restic --repo /srv/treehouse/restic-repo \
	    --password-file /etc/treehouse/restic.password \
	    restore latest --target /tmp/restore-drill && \
	  ls /tmp/restore-drill/srv/treehouse/'

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

test-live:                 ## Hit the deployed VM at 10.10.10.1 (after `make up`)
	TREEHOUSE_HOST=10.10.10.1 .venv/bin/python -m pytest tests/test_live.py -v

test-all:                  ## All suites (schemas + compose + live)
	$(MAKE) test-schemas
	$(MAKE) test-compose
	$(MAKE) test-live

test-deps:                 ## Install python test deps into a local .venv
	python3 -m venv .venv
	./.venv/bin/pip install -r tests/requirements.txt
	@echo "activate: source .venv/bin/activate"

# ----- toolchain reminder --------------------------------------------------
toolchain:                 ## Print install command for missing host packages
	@echo "On Ubuntu 24:"
	@echo "  sudo apt install -y \\"
	@echo "    libvirt-daemon-system libvirt-clients qemu-system-x86 qemu-utils \\"
	@echo "    virtinst genisoimage \\"
	@echo "    ansible \\"
	@echo "    docker.io docker-compose-v2"
	@echo "  sudo usermod -aG libvirt,kvm,docker \$$(whoami)"
	@echo "  newgrp libvirt   # or log out and back in"

clean:                     ## Remove .cache, .venv, ansible cache, .pytest_cache
	rm -rf .cache .venv ansible/.ansible .pytest_cache schemas/.pytest_cache tests/__pycache__ schemas/__pycache__ treehouse/__pycache__ treehouse/*/__pycache__
