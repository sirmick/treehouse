"""
Schema for manifest.yml — the declarative content source-of-truth.

Locked in milestone 1 even though no orchestrator reads it yet, so the
file format is stable before the updater (Phase 3) and the eventual
admin UI bind to it.

See docs/content.md for the design.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ----- ZIM ------------------------------------------------------------------

class ZimEntry(_Strict):
    """A Kiwix ZIM. Pulled from library.kiwix.org's OPDS catalog."""
    name: str = Field(
        description="Catalog name, e.g. 'wikipedia_en_for_schools'. "
                    "No date suffix — the updater resolves the latest."
    )
    keep: int | Literal["latest"] = Field(
        default="latest",
        description="'latest' = retain only newest. Integer N = retain N most recent.",
    )


# ----- YouTube --------------------------------------------------------------

class YoutubeEntry(_Strict):
    """A YouTube channel or playlist, downloaded via yt-dlp into PeerTube."""
    channel: HttpUrl | None = None
    playlist: HttpUrl | None = None
    label: str | None = None
    since: date | None = Field(
        default=None,
        description="Only fetch videos published on/after this date.",
    )
    max_videos: int | None = Field(
        default=None,
        description="Cap on initial backfill. None = unlimited.",
    )


# ----- Kolibri --------------------------------------------------------------

class KolibriEntry(_Strict):
    token: str = Field(description="Kolibri channel token from Kolibri Studio.")
    name: str = Field(description="Display name for the channel.")


# ----- OSM ------------------------------------------------------------------

class OsmEntry(_Strict):
    region: str = Field(
        description="Geofabrik region path, e.g. 'europe/great-britain'."
    )
    provider: Literal["geofabrik"] = "geofabrik"


# ----- Calibre --------------------------------------------------------------

class CalibreFeedFilter(_Strict):
    tags_any: list[str] | None = None
    tags_all: list[str] | None = None


class CalibreEntry(_Strict):
    feed: HttpUrl | None = None
    url: HttpUrl | None = None
    title: str | None = None
    filter: CalibreFeedFilter | None = None


# ----- AI -------------------------------------------------------------------

class AiConfig(_Strict):
    model: str = Field(
        default="llama3.2:3b-q4_K_M",
        description="Ollama model tag.",
    )
    pre_pull: bool = False


# ----- Policies and profiles -----------------------------------------------

class Policies(_Strict):
    bandwidth_limit: str = Field(
        default="10M",
        description="Per-run global cap, e.g. '10M' for 10 MB/s.",
    )
    disk_reserve: str = Field(
        default="50GB",
        description="Free space the updater refuses to dip below.",
    )
    prune_old_versions: bool = True
    notify: Literal["file", "email", "webhook"] = "file"


class Profile(_Strict):
    inherit: list[str] | None = None
    zims: list[str] | None = None
    youtube: list[YoutubeEntry] | None = None
    kolibri: list[KolibriEntry] | None = None
    osm: list[OsmEntry] | None = None
    calibre: list[CalibreEntry] | None = None


# ----- Top level ------------------------------------------------------------

class Manifest(_Strict):
    """Top-level manifest.yml. All sections optional; updater plugins
    that find no entries simply do nothing."""
    zims: list[ZimEntry] = Field(default_factory=list)
    youtube: list[YoutubeEntry] = Field(default_factory=list)
    kolibri: list[KolibriEntry] = Field(default_factory=list)
    osm: list[OsmEntry] = Field(default_factory=list)
    calibre: list[CalibreEntry] = Field(default_factory=list)
    ai: AiConfig | None = None
    policies: Policies = Field(default_factory=Policies)
    profiles: dict[str, Profile] = Field(default_factory=dict)
