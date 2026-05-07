"""Round-trip tests for the schemas. Run with `python -m pytest schemas/`."""

from pathlib import Path

import pytest
import yaml

from schemas import KidsConfig, Manifest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_minimal_manifest():
    m = Manifest.model_validate({})
    assert m.zims == []
    assert m.policies.disk_reserve == "50GB"


def test_manifest_full_example():
    raw = yaml.safe_load("""
zims:
  - name: wikipedia_en_for_schools
  - name: wiktionary_en_simple_all_nopic
    keep: 2

youtube:
  - channel: https://www.youtube.com/@3blue1brown
    max_videos: 50

kolibri:
  - token: 95a52b386f2c485cb97dd60901674a98
    name: "Khan Academy English"

osm:
  - region: europe/great-britain
    provider: geofabrik

calibre:
  - feed: https://standardebooks.org/feeds/atom/all
    filter:
      tags_any: [children, fairy-tales]

ai:
  model: llama3.2:3b-q4_K_M

policies:
  bandwidth_limit: 10M
  disk_reserve: 50GB

profiles:
  travel:
    zims: [wikipedia_for_schools]
""")
    m = Manifest.model_validate(raw)
    assert len(m.zims) == 2
    assert m.zims[1].keep == 2
    assert m.kolibri[0].name == "Khan Academy English"


def test_manifest_rejects_unknown_field():
    with pytest.raises(Exception):
        Manifest.model_validate({"zims": [{"name": "x", "BOGUS": True}]})


def test_repo_manifest_validates():
    """The committed manifest.yml at the repo root must validate against
    the schema — typos or removed fields here are caught at test time
    rather than at the next seed/updater run."""
    manifest_path = REPO_ROOT / "manifest.yml"
    assert manifest_path.exists(), f"missing {manifest_path}"
    raw = yaml.safe_load(manifest_path.read_text())
    Manifest.model_validate(raw)


def test_minimal_kids():
    k = KidsConfig.model_validate({})
    assert k.kids == []


def test_kids_full_example():
    raw = yaml.safe_load("""
kids:
  - id: alice
    name: Alice
    age: 8
    age_band: "6-10"
    avatar: avatars/alice.png
    pin: "1234"
    permissions:
      messaging: true
      messaging_contacts: [grandma]
      ai_companion: true

contacts:
  - id: grandma
    name: Grandma
    avatar: avatars/grandma.png
    role: family
""")
    k = KidsConfig.model_validate(raw)
    assert k.kids[0].id == "alice"
    assert k.kids[0].permissions.ai_companion is True


def test_kids_id_must_be_lowercase_kebab():
    with pytest.raises(Exception):
        KidsConfig.model_validate({"kids": [{
            "id": "Alice",
            "name": "Alice",
            "age": 8,
            "age_band": "6-10",
            "avatar": "x.png",
        }]})
