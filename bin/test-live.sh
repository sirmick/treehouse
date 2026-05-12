#!/usr/bin/env bash
# Run tests/test_live.py against the kids segment.
#
# Reachability depends on network.mode:
#
#   lan      — VM is on the LAN via libvirt's 'lan' network, bridged onto
#              the host's br-lan. Laptop and VM share L2, so pytest runs
#              directly from here pointed at network.lan.ip.
#   isolated — VM only reachable from the kids' AP (or via qemu-ga over
#              virtio-serial, which doesn't help HTTP probes). pytest from
#              this laptop isn't possible; we exit with guidance instead.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="$("$REPO_ROOT/bin/cfg" network.mode)"

case "$MODE" in
  lan)
    VM_IP="$("$REPO_ROOT/bin/cfg" network.lan.ip | cut -d/ -f1)"
    export TREEHOUSE_HOST="$VM_IP"
    # Match the other test targets: use the repo's .venv (created by
    # `make test-deps`). Fall back to system python3 if the venv isn't
    # set up yet, so the failure mode is a clearer pytest-not-found.
    PY="$REPO_ROOT/.venv/bin/python"
    [[ -x "$PY" ]] || PY=python3
    exec "$PY" -m pytest "$REPO_ROOT/tests/test_live.py" -v
    ;;
  isolated)
    cat >&2 <<EOF
[test-live] isolated-mode VMs aren't reachable from this laptop.

  Run from a device on the kids' AP (tablet, phone, AP shell):
    TREEHOUSE_HOST=$("$REPO_ROOT/bin/cfg" network.isolated.host_ip) \\
      python -m pytest tests/test_live.py -v

  Or use bin/verify-isolation-via-tablet.sh for the verification probe.
EOF
    exit 2
    ;;
  *)
    echo "[test-live] unknown network.mode: '$MODE'" >&2
    exit 2
    ;;
esac
