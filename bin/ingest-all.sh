#!/usr/bin/env bash
# Walk a hardcoded list of ZIMs and ingest each into MeiliSearch.
#
# Each entry is "<kiwix-dir>:<catalog-name>:<version-date>:<source-label>:<kind>".
# kiwix-dir is the URL path component under download.kiwix.org/zim/;
# source-label is what gets stored in the document's `source` field
# (rendered by the launcher); kind is article/definition/etc.
#
# Until the Phase-3 updater lands, this is the bridge between
# manifest.yml's catalog names and concrete download URLs. Bump the
# version-date as new builds appear at <https://download.kiwix.org/zim/>.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ZIMS=(
  "wikipedia:wikipedia_en_simple_all_maxi:2026-02:wikipedia:article"
  "wiktionary:wiktionary_en_simple_all_nopic:2026-04:wiktionary:definition"
  "vikidia:vikidia_en_all_maxi:2026-03:vikidia:article"
)

MEILI_URL="${MEILI_URL:-http://127.0.0.1:7700}"
INDEX="${TREEHOUSE_INDEX:-treehouse}"

mkdir -p "$REPO_ROOT/content/zims"

for spec in "${ZIMS[@]}"; do
  IFS=":" read -r kiwix_dir catalog version source kind <<< "$spec"
  filename="${catalog}_${version}.zim"
  local_path="$REPO_ROOT/content/zims/${filename}"
  url="https://download.kiwix.org/zim/${kiwix_dir}/${filename}"

  if [[ ! -f "$local_path" ]]; then
    echo "==> downloading $filename"
    curl -L --fail --progress-bar -o "${local_path}.partial" "$url"
    mv "${local_path}.partial" "$local_path"
  else
    echo "==> $filename already present"
  fi

  echo "==> ingesting $catalog (source=$source, kind=$kind)"
  "$REPO_ROOT/.venv/bin/python" -m treehouse.searchd.ingest_kiwix \
    --zim "$local_path" \
    --source "$source" \
    --kind "$kind" \
    --meili-url "$MEILI_URL" \
    --index "$INDEX"
done

echo
echo "all ZIMs ingested into '$INDEX'"
