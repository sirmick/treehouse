#!/usr/bin/env bash
# Spins up a throwaway "tablet" VM with a SINGLE NIC on br-kids — no
# management path, no SSH, no escape route — and runs bin/verify-isolation
# inside it via cloud-init. Probe output is captured from a file-backed
# serial console; the VM is destroyed on exit regardless of outcome.
#
# Why a separate VM (not a netns on the host bridge): a real guest on
# br-kids exercises the segment exactly the way a kid's tablet would.
# A host-side netns shares the host kernel's routing tables, fwmarks,
# and conntrack — too easy to get a false pass.
#
# Preconditions: the treehouse VM is up (so its dnsmasq is handing out
# leases on br-kids), and the cached debian-12 cloud image exists.
#
# Output: the probe's stdout is mirrored to this terminal. Exit code is
# the probe's exit code (0 = pass=N fail=0).

set -euo pipefail

export LIBVIRT_DEFAULT_URI="qemu:///system"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE="${TREEHOUSE_STORAGE_DIR:-/var/lib/libvirt/images/treehouse}"
NAME="${TREEHOUSE_TABLET_VM_NAME:-treehouse-tablet}"
PROBE="$REPO_ROOT/bin/verify-isolation"
BASE_IMAGE="$STORAGE/debian-12-genericcloud-amd64.qcow2"
DISK="$STORAGE/$NAME-system.qcow2"
SEED_DIR="$STORAGE/$NAME-seed"
SEED_ISO="$STORAGE/$NAME-seed.iso"
CONSOLE="$STORAGE/$NAME-console.log"
WAIT_SECS="${TREEHOUSE_TABLET_TIMEOUT:-300}"

log() { printf '\033[36m[tablet]\033[0m %s\n' "$*"; }

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need virsh
need virt-install
need qemu-img
need base64
command -v genisoimage >/dev/null && ISO_TOOL=genisoimage || { need mkisofs; ISO_TOOL=mkisofs; }

[[ -f "$PROBE" ]] || { echo "probe not found: $PROBE" >&2; exit 1; }
[[ -f "$BASE_IMAGE" ]] || { echo "base image not found: $BASE_IMAGE — run 'make up' first" >&2; exit 1; }
virsh net-info br-kids >/dev/null 2>&1 || { echo "br-kids network not defined — run 'make up' first" >&2; exit 1; }

destroy_vm() {
  virsh destroy "$NAME" >/dev/null 2>&1 || true
  virsh undefine "$NAME" --nvram >/dev/null 2>&1 || true
  rm -f "$DISK" "$SEED_ISO"
  rm -rf "$SEED_DIR"
}

# EXIT trap: always destroy the VM and remove the throwaway disk + seed.
# The console log is intentionally preserved at $CONSOLE for forensics.
trap 'destroy_vm' EXIT

# Idempotent reset in case a previous run died before its trap fired.
destroy_vm
rm -f "$CONSOLE"
: > "$CONSOLE"

log "preparing throwaway disk + seed"
qemu-img create -q -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$DISK"

PROBE_B64="$(base64 -w0 "$PROBE")"

mkdir -p "$SEED_DIR"
cat > "$SEED_DIR/meta-data" <<EOF
instance-id: $NAME-$(date +%s)
local-hostname: $NAME
EOF

# The probe writes to stdout/stderr; we redirect those to /dev/ttyS0 so
# they land in the file-backed serial regardless of journald/rsyslog.
# After the probe, print TABLET_DONE_EXIT=<code> as a sentinel for the
# host-side wait loop, then poweroff.
cat > "$SEED_DIR/user-data" <<EOF
#cloud-config
hostname: $NAME
manage_etc_hosts: true
ssh_pwauth: false
disable_root: true
package_update: false
write_files:
  - path: /usr/local/bin/verify-isolation
    permissions: '0755'
    encoding: b64
    content: $PROBE_B64
runcmd:
  - |
    {
      echo "==> tablet probe starting"
      sh /usr/local/bin/verify-isolation
      echo "TABLET_DONE_EXIT=\$?"
    } > /dev/ttyS0 2>&1
  - poweroff
EOF

$ISO_TOOL -quiet -output "$SEED_ISO" -volid cidata -joliet -rock \
  "$SEED_DIR/meta-data" "$SEED_DIR/user-data"

log "starting $NAME (single NIC on br-kids, no mgmt path)"
virt-install \
  --connect qemu:///system \
  --name "$NAME" \
  --memory 512 \
  --vcpus 1 \
  --osinfo debian12 \
  --disk path="$DISK",bus=virtio \
  --disk path="$SEED_ISO",bus=virtio,readonly=on \
  --network network=br-kids,model=virtio \
  --graphics none \
  --serial file,source.path="$CONSOLE",source.seclabel.model=dac,source.seclabel.relabel=yes,source.seclabel.label="+$(id -u):+$(id -g)" \
  --noautoconsole \
  --import >/dev/null

log "waiting up to ${WAIT_SECS}s for probe to finish (sentinel: TABLET_DONE_EXIT=)"
deadline=$(( $(date +%s) + WAIT_SECS ))
while [[ $(date +%s) -lt $deadline ]]; do
  if grep -q '^TABLET_DONE_EXIT=' "$CONSOLE" 2>/dev/null; then
    break
  fi
  sleep 2
done

if ! grep -q '^TABLET_DONE_EXIT=' "$CONSOLE" 2>/dev/null; then
  echo >&2
  echo "tablet probe did not finish within ${WAIT_SECS}s." >&2
  echo "console log preserved at: $CONSOLE" >&2
  exit 1
fi

# Echo the probe section to the operator's terminal.
sed -n '/==> tablet probe starting/,/^TABLET_DONE_EXIT=/p' "$CONSOLE"

EXIT_LINE="$(grep '^TABLET_DONE_EXIT=' "$CONSOLE" | tail -1)"
TABLET_RC="${EXIT_LINE##*=}"
TABLET_RC="${TABLET_RC%%[!0-9]*}"
: "${TABLET_RC:=1}"

log "console log preserved at: $CONSOLE"
exit "$TABLET_RC"
