#!/usr/bin/env bash
# Run tests/test_live.py from the laptop against the kids segment.
#
# 10.10.10.1 lives on the VM's enp2s0 (the kids NIC on br-kids), which
# is libvirt-isolated — the host has no route to it, by design. We
# tunnel via SSH local-forward through the management interface so the
# HTTP requests originate inside the VM, where 10.10.10.1 is local.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INV="$REPO_ROOT/ansible/inventory/libvirt"
PORT="${TREEHOUSE_TEST_PORT:-18080}"

[[ -f "$INV" ]] || { echo "no inventory at $INV — run 'make up'" >&2; exit 1; }

IP="$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' "$INV")"
[[ -n "$IP" ]] || { echo "couldn't parse mgmt IP from $INV" >&2; exit 1; }

echo "[test-live] forwarding 127.0.0.1:$PORT → 10.10.10.1:80 via mick@$IP"
ssh -N -o ExitOnForwardFailure=yes -L "$PORT:10.10.10.1:80" "mick@$IP" &
SSHPID=$!
trap 'kill "$SSHPID" 2>/dev/null || true' EXIT

# Wait for the forward to actually accept connections — pytest's first
# request races ssh setup otherwise.
for _ in $(seq 1 20); do
  if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then exec 3<&-; break; fi
  sleep 0.2
done

TREEHOUSE_HOST="127.0.0.1:$PORT" \
  "$REPO_ROOT/.venv/bin/python" -m pytest "$REPO_ROOT/tests/test_live.py" -v
