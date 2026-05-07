#!/usr/bin/env bash
# Tear down the treehouse VM. Idempotent.
#
# Removes the VM definition and all associated disks. Leaves the
# br-kids libvirt network in place (cheap to keep, no resources used
# while idle).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$REPO_ROOT/.cache"
NAME="${TREEHOUSE_VM_NAME:-treehouse}"

log() { printf '\033[36m[down]\033[0m %s\n' "$*"; }

if virsh dominfo "$NAME" >/dev/null 2>&1; then
  log "destroying $NAME"
  virsh destroy "$NAME" 2>/dev/null || true
  virsh undefine "$NAME" --remove-all-storage 2>/dev/null || \
    virsh undefine "$NAME"
fi

# Clean up cached disks if undefine didn't remove them
rm -f \
  "$CACHE/$NAME-system.qcow2" \
  "$CACHE/$NAME-content.qcow2" \
  "$CACHE/$NAME-seed.iso"
rm -rf "$CACHE/$NAME-seed"

log "done — base image at $CACHE/debian-12-genericcloud-amd64.qcow2 retained for reuse"
