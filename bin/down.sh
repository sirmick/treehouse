#!/usr/bin/env bash
# Tear down the treehouse VM. Idempotent.
#
# Removes the VM definition and all associated disks. Leaves the
# br-kids libvirt network in place (cheap to keep, no resources used
# while idle).

set -euo pipefail

export LIBVIRT_DEFAULT_URI="qemu:///system"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE="${TREEHOUSE_STORAGE_DIR:-/var/lib/libvirt/images/treehouse}"
NAME="${TREEHOUSE_VM_NAME:-treehouse}"

log() { printf '\033[36m[down]\033[0m %s\n' "$*"; }

if virsh dominfo "$NAME" >/dev/null 2>&1; then
  log "destroying $NAME"
  virsh destroy "$NAME" 2>/dev/null || true
  virsh undefine "$NAME" --remove-all-storage 2>/dev/null || \
    virsh undefine "$NAME"
fi

# Clean up per-VM artifacts if undefine didn't remove them
rm -f \
  "$STORAGE/$NAME-system.qcow2" \
  "$STORAGE/$NAME-content.qcow2" \
  "$STORAGE/$NAME-seed.iso" \
  "$STORAGE/$NAME-console.log"
rm -rf "$STORAGE/$NAME-seed"

log "done — base image at $STORAGE/debian-12-genericcloud-amd64.qcow2 retained for reuse"
