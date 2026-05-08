#!/usr/bin/env bash
# Tear down the treehouse VM. Idempotent.
#
# Removes the VM definition and ephemeral disks (system, seed ISO,
# console log). The content disk is PRESERVED — ZIMs and other large
# content survive `make down` so a rebuild doesn't re-download.
#
# To nuke the content disk too, run `make wipe-content` (or rm
# $STORAGE/$NAME-content.qcow2 manually).
#
# Leaves the br-kids libvirt network in place (cheap to keep).

set -euo pipefail

export LIBVIRT_DEFAULT_URI="qemu:///system"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE="${TREEHOUSE_STORAGE_DIR:-/var/lib/libvirt/images/treehouse}"
NAME="${TREEHOUSE_VM_NAME:-treehouse}"

log() { printf '\033[36m[down]\033[0m %s\n' "$*"; }

if virsh dominfo "$NAME" >/dev/null 2>&1; then
  log "destroying $NAME"
  virsh destroy "$NAME" 2>/dev/null || true
  # `undefine` without --remove-all-storage so the content disk
  # qcow2 stays on disk. We then explicitly rm only the ephemeral
  # files below.
  virsh undefine "$NAME" --nvram 2>/dev/null || true
fi

# Ephemeral files only — content disk intentionally not listed.
rm -f \
  "$STORAGE/$NAME-system.qcow2" \
  "$STORAGE/$NAME-seed.iso" \
  "$STORAGE/$NAME-console.log"
rm -rf "$STORAGE/$NAME-seed"

if [[ -f "$STORAGE/$NAME-content.qcow2" ]]; then
  log "preserved $NAME-content.qcow2 — re-attached on next 'make up'"
fi

log "done — base image at $STORAGE/debian-12-genericcloud-amd64.qcow2 retained for reuse"
