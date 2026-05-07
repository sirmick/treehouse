"""
Schema for kids.yml — the declarative kids-and-permissions source-of-truth.

Locked in milestone 1 even though no provisioner reads it yet, so the
file format is stable before identity work (Phase 2) and the eventual
admin UI bind to it.

See docs/identity.md for the design.
"""

from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


AgeBand = Literal["0-3", "3-7", "6-10", "9-13", "12-16"]


class ServicePermission(_Strict):
    enabled: bool = True
    curfew_after: time | None = None
    daily_limit_minutes: int | None = None


class SearchPreferences(_Strict):
    prefer_kinds: list[str] | None = None
    avoid_kinds: list[str] | None = None


class Permissions(_Strict):
    messaging: bool = True
    messaging_contacts: list[str] = Field(default_factory=list)
    activities: Literal["all"] | list[str] = "all"
    ai_companion: bool = True
    services: dict[str, ServicePermission] = Field(default_factory=dict)
    search_preferences: SearchPreferences | None = None
    videos_curfew_after: time | None = None


class Kid(_Strict):
    id: str = Field(
        pattern=r"^[a-z][a-z0-9-]*$",
        description="Stable username, used verbatim in every backend. Never changes.",
    )
    name: str
    age: int = Field(ge=0, le=18)
    age_band: AgeBand
    avatar: str
    pin: str | None = Field(
        default=None,
        pattern=r"^[0-9]{4,6}$",
        description="4-6 digit numeric PIN. None means dedicated-device, no PIN.",
    )
    theme: str | None = None
    ephemeral: bool = Field(
        default=False,
        description="Session state wiped on logout. Used for guest tile.",
    )
    dedicated_device: bool = Field(
        default=False,
        description="If true, browsers may auto-pick this kid via lastUsedKid cookie.",
    )
    permissions: Permissions = Field(default_factory=Permissions)


class Contact(_Strict):
    """Adults / family who appear in messaging contact lists.
    Not pickable on the avatar grid."""
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    avatar: str
    role: Literal["parent", "family", "friend"]


class KidsConfig(_Strict):
    """Top-level kids.yml."""
    kids: list[Kid] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
