#!/usr/bin/env bash
# Run tests/test_live.py from the laptop against the kids segment.
#
# How we reach the box depends on network.mode in treehouse.yml:
#
#   isolated — 10.10.10.1 lives on the VM's kids NIC (libvirt-isolated;
#              the host has no route). Tunnel via SSH local-forward
#              through the management NIC so HTTP requests originate
#              inside the VM, where 10.10.10.1 is local.
#
#   lan      — VM has a static IP on the LAN; the laptop reaches it
#              directly. No tunnel needed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="$("$REPO_ROOT/bin/cfg" network.mode)"

case "$MODE" in
  isolated)
    INV="$REPO_ROOT/ansible/inventory/libvirt"
    PORT="${TREEHOUSE_TEST_PORT:-18080}"

    [[ -f "$INV" ]] || { echo "no inventory at $INV — run 'make up'" >&2; exit 1; }

    MGMT_IP="$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' "$INV")"
    [[ -n "$MGMT_IP" ]] || { echo "couldn't parse mgmt IP from $INV" >&2; exit 1; }

    KIDS_IP="$("$REPO_ROOT/bin/cfg" network.isolated.host_ip)"

    echo "[test-live] mode=isolated; forwarding 127.0.0.1:$PORT → ${KIDS_IP}:80 via mick@${MGMT_IP}"
    ssh -N -o ExitOnForwardFailure=yes -L "${PORT}:${KIDS_IP}:80" "mick@${MGMT_IP}" &
    SSHPID=$!
    trap 'kill "$SSHPID" 2>/dev/null || true' EXIT

    # Wait for the forward to actually accept connections — pytest's
    # first request races ssh setup otherwise.
    for _ in $(seq 1 20); do
      if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then exec 3<&-; break; fi
      sleep 0.2
    done

    TREEHOUSE_HOST="127.0.0.1:${PORT}"
    ;;

  lan)
    LAN_IP="$("$REPO_ROOT/bin/cfg" network.lan.ip | cut -d/ -f1)"
    echo "[test-live] mode=lan; hitting ${LAN_IP}:80 directly"
    TREEHOUSE_HOST="${LAN_IP}"
    ;;

  *)
    echo "unknown network.mode in treehouse.yml: '$MODE' (expected: isolated | lan)" >&2
    exit 1
    ;;
esac

TREEHOUSE_HOST="$TREEHOUSE_HOST" \
  "$REPO_ROOT/.venv/bin/python" -m pytest "$REPO_ROOT/tests/test_live.py" -v
