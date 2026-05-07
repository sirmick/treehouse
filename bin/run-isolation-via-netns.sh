#!/usr/bin/env bash
# Runs INSIDE the treehouse VM (via `make verify-isolation`).
#
# Creates an ephemeral 'tablet' netns, attaches a macvlan child of eth1
# (the kids-segment NIC), gets DHCP from the VM's dnsmasq, runs
# /tmp/verify-isolation inside that namespace, cleans up.
#
# This simulates a tablet plugged into the kids' WiFi: only path is
# the kids' segment, no upstream, no NAT.

set -euo pipefail

NS=tablet
VETH=tablet-tap
KIDS_IFACE="${KIDS_IFACE:-eth1}"

cleanup() {
  ip netns del "$NS" 2>/dev/null || true
}
trap cleanup EXIT

# Fresh namespace
ip netns del "$NS" 2>/dev/null || true
ip netns add "$NS"

# Macvlan child of the kids interface, attached only to the netns.
ip link add "$VETH" link "$KIDS_IFACE" type macvlan mode bridge
ip link set "$VETH" netns "$NS"
ip netns exec "$NS" ip link set lo up
ip netns exec "$NS" ip link set "$VETH" up

# DHCP from dnsmasq inside the netns.
ip netns exec "$NS" udhcpc -i "$VETH" -t 10 -T 2 -q -n 2>/dev/null || \
  ip netns exec "$NS" dhclient -1 "$VETH" || {
    echo "DHCP failed inside namespace" >&2
    exit 1
  }

# Run the probe.
ip netns exec "$NS" /tmp/verify-isolation
