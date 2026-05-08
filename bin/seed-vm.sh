#!/usr/bin/env bash
# Download every ZIM listed below to the VM's /srv/treehouse/content/zims/,
# add each to kiwix's library, then restart the kiwix container so it
# picks them up.
#
# Tuples mirror bin/ingest-all.sh — the same (kiwix-dir, catalog-name,
# version-date) lives in both, by hand. This duplication goes away
# when the Phase-3 updater lands and consumes manifest.yml directly.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INV="$REPO_ROOT/ansible/inventory/libvirt"

[[ -f "$INV" ]] || { echo "no inventory at $INV — run 'make up'" >&2; exit 1; }

IP="$(awk '/ansible_host=/ {sub(/.*ansible_host=/,""); sub(/ .*/,""); print}' "$INV")"
[[ -n "$IP" ]] || { echo "couldn't parse mgmt IP from $INV" >&2; exit 1; }

ZIMS=(
  "wikipedia:wikipedia_en_simple_all_maxi:2026-02"
  "wiktionary:wiktionary_en_simple_all_nopic:2026-04"
  "vikidia:vikidia_en_all_maxi:2026-03"
)

# Run a single SSH session that downloads and registers each ZIM.
# Using a heredoc keeps the loop on the VM side, which avoids one
# round-trip per ZIM and means we can tail the curl output live.
ssh "mick@$IP" "sudo bash -s" <<EOF
set -euo pipefail

cd /srv/treehouse/content/zims

declare -a zims=(
$(for z in "${ZIMS[@]}"; do echo "  \"$z\""; done)
)

for spec in "\${zims[@]}"; do
  IFS=":" read -r kiwix_dir catalog version <<< "\$spec"
  filename="\${catalog}_\${version}.zim"
  url="https://download.kiwix.org/zim/\${kiwix_dir}/\${filename}"

  if [[ -f "\$filename" ]]; then
    echo "==> \$filename already on disk"
  else
    echo "==> downloading \$filename"
    curl -L --fail -o "\${filename}.partial" "\$url"
    mv "\${filename}.partial" "\$filename"
  fi

  echo "==> registering \$filename in kiwix library"
  docker exec --user root treehouse-kiwix \\
    kiwix-manage /data/library.xml add "/data/\$filename" \\
    || true   # add is idempotent-ish; silence "already present"
done

echo "==> restarting kiwix to pick up library.xml"
docker restart treehouse-kiwix
EOF
