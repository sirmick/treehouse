#!/usr/bin/env bash
# Bring up the treehouse VM via libvirt + virt-install + cloud-init.
#
# Replaces Vagrant. OOTB on Ubuntu 24 once libvirt-* is installed.
#
# Idempotent — re-running with the VM already up is a no-op.

set -euo pipefail

# Ensure all virsh calls hit the system scope. Without this, virsh defaults to
# qemu:///session for non-root users and creates a phantom per-user network
# that can't bind real bridge interfaces.
export LIBVIRT_DEFAULT_URI="qemu:///system"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# VM disks live where libvirt-qemu can read them without /home traversal.
# One-time setup: sudo mkdir -p $STORAGE && sudo chown $(whoami): $STORAGE
STORAGE="${TREEHOUSE_STORAGE_DIR:-/var/lib/libvirt/images/treehouse}"
NAME="${TREEHOUSE_VM_NAME:-treehouse}"
MEMORY="${TREEHOUSE_VM_MEMORY:-8192}"
VCPUS="${TREEHOUSE_VM_VCPUS:-4}"
SYSTEM_DISK_GB="${TREEHOUSE_SYSTEM_DISK_GB:-40}"
CONTENT_DISK_GB="${TREEHOUSE_CONTENT_DISK_GB:-200}"
BASE_IMAGE_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
BASE_IMAGE="$STORAGE/debian-12-genericcloud-amd64.qcow2"
NET_XML="$REPO_ROOT/ansible/files/br-kids.xml"

# Read network mode from treehouse.yml. The VM has a single NIC on
# the kids segment — no separate mgmt NIC. Control from the host is
# via qemu-guest-agent over virtio-serial (community.libvirt.libvirt_qemu
# in ansible). This matches the Pi-production shape.
NETWORK_MODE="$("$REPO_ROOT/bin/cfg" network.mode)"
case "$NETWORK_MODE" in
  isolated)
    KIDS_NIC_ARG="network=$("$REPO_ROOT/bin/cfg" network.isolated.bridge),model=virtio"
    ;;
  lan)
    LAN_IFACE="$("$REPO_ROOT/bin/cfg" network.lan.host_iface)"
    KIDS_NIC_ARG="type=direct,source=${LAN_IFACE},source.mode=bridge,model=virtio"
    ;;
  *)
    echo "unknown network.mode in treehouse.yml: '$NETWORK_MODE' (expected: isolated | lan)" >&2
    exit 1
    ;;
esac

if [[ ! -d "$STORAGE" || ! -w "$STORAGE" ]]; then
  echo "missing or unwritable: $STORAGE" >&2
  echo "one-time setup:" >&2
  echo "  sudo mkdir -p $STORAGE && sudo chown $(whoami): $STORAGE" >&2
  exit 1
fi

log() { printf '\033[36m[up]\033[0m %s\n' "$*"; }

# ----- prerequisites --------------------------------------------------------
need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need virsh
need virt-install
need qemu-img
need genisoimage 2>/dev/null || need mkisofs

# ----- libvirt network ------------------------------------------------------
# Only the isolated mode uses libvirt's br-kids network. In lan mode the
# second NIC is a macvtap child of a host interface — no libvirt network
# object needed.
if [[ "$NETWORK_MODE" == "isolated" ]]; then
  if ! virsh net-info br-kids >/dev/null 2>&1; then
    log "defining br-kids libvirt network"
    virsh net-define "$NET_XML"
    virsh net-autostart br-kids
    virsh net-start br-kids
  else
    log "br-kids network already defined"
    # grep without -q so virsh can finish writing — under `set -o pipefail`,
    # `grep -q` matches early, closes stdin, and virsh dies with SIGPIPE,
    # making the pipeline look failed even when Active: yes.
    virsh net-info br-kids | grep 'Active:.*yes' >/dev/null || virsh net-start br-kids
  fi
else
  log "network.mode=lan — skipping br-kids; second NIC will macvtap-bridge to ${LAN_IFACE}"
fi

# ----- base image -----------------------------------------------------------
if [[ ! -f "$BASE_IMAGE" ]]; then
  log "downloading Debian 12 cloud image (~600 MB)"
  wget -q --show-progress -O "$BASE_IMAGE.partial" "$BASE_IMAGE_URL"
  mv "$BASE_IMAGE.partial" "$BASE_IMAGE"
fi

# ----- per-VM disk ----------------------------------------------------------
SYSTEM_DISK="$STORAGE/$NAME-system.qcow2"
CONTENT_DISK="$STORAGE/$NAME-content.qcow2"

if ! virsh dominfo "$NAME" >/dev/null 2>&1; then
  log "creating $NAME system disk (${SYSTEM_DISK_GB}G overlay on base image)"
  qemu-img create -q -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$SYSTEM_DISK"
  qemu-img resize -q "$SYSTEM_DISK" "${SYSTEM_DISK_GB}G"

  if [[ -f "$CONTENT_DISK" ]]; then
    # Content disk persists across `make down`/`make up` so ZIMs and
    # other large content survive a system rebuild. To wipe it, run
    # `make wipe-content` (or rm the qcow2 manually).
    log "$NAME content disk exists — reusing (preserves ZIMs)"
  else
    log "creating $NAME content disk (${CONTENT_DISK_GB}G sparse)"
    qemu-img create -q -f qcow2 "$CONTENT_DISK" "${CONTENT_DISK_GB}G"
  fi
fi

# ----- cloud-init seed ------------------------------------------------------
SSH_KEY=""
for k in "$HOME"/.ssh/id_ed25519.pub "$HOME"/.ssh/id_rsa.pub "$HOME"/.ssh/id_ecdsa.pub; do
  [[ -f "$k" ]] && SSH_KEY="$(cat "$k")" && break
done
if [[ -z "$SSH_KEY" ]]; then
  log "no SSH key found — generating ed25519 keypair"
  ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519"
  SSH_KEY="$(cat "$HOME/.ssh/id_ed25519.pub")"
fi

SEED_DIR="$STORAGE/$NAME-seed"
SEED_ISO="$STORAGE/$NAME-seed.iso"

mkdir -p "$SEED_DIR"
cat > "$SEED_DIR/meta-data" <<EOF
instance-id: $NAME-$(date +%s)
local-hostname: $NAME
EOF

cat > "$SEED_DIR/user-data" <<EOF
#cloud-config
hostname: $NAME
fqdn: $NAME.local
manage_etc_hosts: true
ssh_pwauth: false
disable_root: false
users:
  - name: mick
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    groups: [sudo, docker]
    ssh_authorized_keys:
      - $SSH_KEY
  - name: root
    ssh_authorized_keys:
      - $SSH_KEY
package_update: true
packages:
  - qemu-guest-agent
runcmd:
  - systemctl enable --now qemu-guest-agent
EOF

if command -v genisoimage >/dev/null; then
  ISO_TOOL=genisoimage
else
  ISO_TOOL=mkisofs
fi
$ISO_TOOL -quiet -output "$SEED_ISO" -volid cidata -joliet -rock \
  "$SEED_DIR/meta-data" "$SEED_DIR/user-data"

# ----- create VM ------------------------------------------------------------
if virsh dominfo "$NAME" >/dev/null 2>&1; then
  log "$NAME already defined — starting if not running"
  virsh dominfo "$NAME" | grep 'State:.*running' >/dev/null || virsh start "$NAME"
else
  log "creating $NAME via virt-install (this takes ~30s)"
  virt-install \
    --connect qemu:///system \
    --name "$NAME" \
    --memory "$MEMORY" \
    --vcpus "$VCPUS" \
    --osinfo debian12 \
    --disk path="$SYSTEM_DISK",bus=virtio \
    --disk path="$CONTENT_DISK",bus=virtio,serial=treehouse-content \
    --disk path="$SEED_ISO",bus=virtio,readonly=on \
    --network ${KIDS_NIC_ARG} \
    --controller type=virtio-serial \
    --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
    --graphics none \
    --serial file,source.path="$STORAGE/$NAME-console.log",source.seclabel.model=dac,source.seclabel.relabel=yes,source.seclabel.label="+$(id -u):+$(id -g)" \
    --noautoconsole \
    --import
fi

# ----- wait for qemu-guest-agent --------------------------------------------
# Single-NIC VMs have no laptop-reachable IP (kids segment is isolated
# in `isolated` mode; macvtap blocks the host in `lan` mode). The way
# in is the guest agent over virtio-serial. Cloud-init installs +
# starts qemu-guest-agent; we just poll until it answers.
log "waiting for qemu-guest-agent..."
for _ in $(seq 1 90); do
  if virsh qemu-agent-command "$NAME" '{"execute":"guest-ping"}' >/dev/null 2>&1; then
    log "qemu-ga ready"
    log ""
    log "next: make provision   (uses ansible/inventory/libvirt-qemu)"
    exit 0
  fi
  sleep 2
done

log "VM is up but qemu-ga didn't respond in 3 min — check console log:"
log "  cat $STORAGE/$NAME-console.log"
exit 1
