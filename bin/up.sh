#!/usr/bin/env bash
# Bring up the treehouse VM via libvirt + virt-install + cloud-init.
#
# Replaces Vagrant. OOTB on Ubuntu 24 once libvirt-* is installed.
#
# Idempotent — re-running with the VM already up is a no-op.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$REPO_ROOT/.cache"
NAME="${TREEHOUSE_VM_NAME:-treehouse}"
MEMORY="${TREEHOUSE_VM_MEMORY:-8192}"
VCPUS="${TREEHOUSE_VM_VCPUS:-4}"
SYSTEM_DISK_GB="${TREEHOUSE_SYSTEM_DISK_GB:-40}"
CONTENT_DISK_GB="${TREEHOUSE_CONTENT_DISK_GB:-200}"
BASE_IMAGE_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
BASE_IMAGE="$CACHE/debian-12-genericcloud-amd64.qcow2"
NET_XML="$REPO_ROOT/ansible/files/br-kids.xml"

mkdir -p "$CACHE"
log() { printf '\033[36m[up]\033[0m %s\n' "$*"; }

# ----- prerequisites --------------------------------------------------------
need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need virsh
need virt-install
need qemu-img
need genisoimage 2>/dev/null || need mkisofs

# ----- libvirt network ------------------------------------------------------
if ! virsh net-info br-kids >/dev/null 2>&1; then
  log "defining br-kids libvirt network"
  virsh net-define "$NET_XML"
  virsh net-autostart br-kids
  virsh net-start br-kids
else
  log "br-kids network already defined"
  virsh net-info br-kids | grep -q 'Active:.*yes' || virsh net-start br-kids
fi

# ----- base image -----------------------------------------------------------
if [[ ! -f "$BASE_IMAGE" ]]; then
  log "downloading Debian 12 cloud image (~600 MB)"
  wget -q --show-progress -O "$BASE_IMAGE.partial" "$BASE_IMAGE_URL"
  mv "$BASE_IMAGE.partial" "$BASE_IMAGE"
fi

# ----- per-VM disk ----------------------------------------------------------
SYSTEM_DISK="$CACHE/$NAME-system.qcow2"
CONTENT_DISK="$CACHE/$NAME-content.qcow2"

if ! virsh dominfo "$NAME" >/dev/null 2>&1; then
  log "creating $NAME system disk (${SYSTEM_DISK_GB}G overlay on base image)"
  qemu-img create -q -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$SYSTEM_DISK"
  qemu-img resize -q "$SYSTEM_DISK" "${SYSTEM_DISK_GB}G"

  log "creating $NAME content disk (${CONTENT_DISK_GB}G sparse)"
  qemu-img create -q -f qcow2 "$CONTENT_DISK" "${CONTENT_DISK_GB}G"
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

SEED_DIR="$CACHE/$NAME-seed"
SEED_ISO="$CACHE/$NAME-seed.iso"

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
      - $SEEDKEY
  - name: root
    ssh_authorized_keys:
      - $SEEDKEY
package_update: true
packages:
  - qemu-guest-agent
runcmd:
  - sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 net.ifnames=0 biosdevname=0"/' /etc/default/grub
  - update-grub
  - systemctl enable --now qemu-guest-agent
EOF
# Inject the actual key (heredoc avoids YAML quoting hell)
sed -i "s|\$SEEDKEY|$SSH_KEY|g" "$SEED_DIR/user-data"

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
  virsh dominfo "$NAME" | grep -q 'State:.*running' || virsh start "$NAME"
else
  log "creating $NAME via virt-install (this takes ~30s)"
  virt-install \
    --connect qemu:///system \
    --name "$NAME" \
    --memory "$MEMORY" \
    --vcpus "$VCPUS" \
    --osinfo debian12 \
    --disk path="$SYSTEM_DISK",bus=virtio \
    --disk path="$CONTENT_DISK",bus=virtio \
    --disk path="$SEED_ISO",device=cdrom \
    --network network=default,model=virtio \
    --network network=br-kids,model=virtio \
    --graphics none \
    --noautoconsole \
    --import
fi

# ----- wait for SSH ---------------------------------------------------------
log "waiting for $NAME to acquire an IP on the management network"
for _ in $(seq 1 60); do
  IP="$(virsh domifaddr "$NAME" --source agent 2>/dev/null | awk '/ipv4/ && $4 ~ /default|192/ {print $4}' | head -1 | cut -d/ -f1 || true)"
  [[ -n "${IP:-}" ]] && break
  IP="$(virsh domifaddr "$NAME" 2>/dev/null | awk '$3 == "ipv4" {print $4}' | head -1 | cut -d/ -f1 || true)"
  [[ -n "${IP:-}" ]] && break
  sleep 2
done

if [[ -z "${IP:-}" ]]; then
  log "could not detect IP — VM may still be booting; try: virsh domifaddr $NAME"
  exit 1
fi

log "treehouse VM at $IP"

# ----- inventory regeneration ----------------------------------------------
INV="$REPO_ROOT/ansible/inventory/libvirt"
mkdir -p "$(dirname "$INV")"
cat > "$INV" <<EOF
[treehouse]
treehouse ansible_host=$IP ansible_user=mick

[treehouse:vars]
ansible_python_interpreter=/usr/bin/python3
EOF
log "wrote $INV"

# ----- ssh probe ------------------------------------------------------------
log "probing SSH..."
for _ in $(seq 1 30); do
  if ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=2 \
       "mick@$IP" 'echo ok' >/dev/null 2>&1; then
    log "ssh to $NAME ok"
    log ""
    log "next: make provision"
    exit 0
  fi
  sleep 2
done

log "VM up but ssh not yet ready — try 'ssh mick@$IP' in a moment"
exit 0
