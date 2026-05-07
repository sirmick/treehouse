"""
Adapter contract — every backend service implements this.

Five identity-side verbs, per content.md's separation of identity from
content-source plugins:

    users_ensure(kids)       provision/update users from kids.yml
    users_remove(kid_id)     deactivate (don't hard-delete)
    auth_token(kid_id)       mint a session usable by the broker redirect
    health()                 quick liveness + a one-line summary
    reload()                 best-effort signal after content/config change

Content lifecycle (plan/apply/prune for ZIM, YouTube, Kolibri, OSM,
Calibre) is a separate plugin protocol, not part of the adapter — see
content.md.

Three orchestrators iterate over the adapter registry:

  - provisioner uses users_ensure / users_remove
  - launcher's broker uses auth_token
  - admin dashboard uses health
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from schemas.kids import Kid


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class Health:
    status: HealthStatus
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Cookie:
    """Cookie the broker should set on the redirect response."""
    name: str
    value: str
    domain: str
    path: str = "/"
    http_only: bool = True
    secure: bool = False  # internal HTTP-only segment
    same_site: str = "Lax"
    max_age: int | None = None


@dataclass(frozen=True)
class Token:
    """Whatever the broker needs to inject so the kid lands logged-in.

    For services that use cookies (Kolibri, PeerTube), `cookies` is set.
    For services that need an Authorization header (Calibre HTTP basic),
    `headers` is set. For services using localStorage (Element, Sugarizer),
    `local_storage` is set and the launcher serves an intermediate page
    that writes those keys before redirecting.
    """
    cookies: list[Cookie] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    local_storage: dict[str, str] = field(default_factory=dict)
    # Optional landing path (e.g. "/dashboard") to redirect to after token
    # injection. Defaults to "/" — the service's own root.
    landing_path: str = "/"


@dataclass(frozen=True)
class Result:
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, summary: str = "ok", **details: Any) -> "Result":
        return cls(True, summary, details)

    @classmethod
    def failure(cls, summary: str, **details: Any) -> "Result":
        return cls(False, summary, details)


class Adapter(ABC):
    """Contract every backend service implements."""

    #: Stable name; matches the service hostname stem and the registry key.
    name: str = ""

    #: Other adapters that must be healthy before this one is provisioned.
    requires: list[str] = []

    # ----- identity lifecycle ----------------------------------------------

    @abstractmethod
    def users_ensure(self, kids: Iterable[Kid]) -> Result:
        """Reconcile the service's users to match the given list.

        Idempotent: re-running with the same input is a no-op. Adapters
        with no user model (Kiwix, tileserver) return success immediately.
        """

    @abstractmethod
    def users_remove(self, kid_id: str) -> Result:
        """Deactivate (not hard-delete) the kid in this service.

        Preserves data for re-enable. Idempotent — re-running on an
        already-removed kid still returns success.
        """

    @abstractmethod
    def auth_token(self, kid_id: str) -> Token | None:
        """Mint a session for the kid; the broker injects this into the
        redirect to the service.

        Returns None when the service has no user model (kid arrives
        anonymously and the service knows what to do).
        """

    # ----- ops -------------------------------------------------------------

    @abstractmethod
    def health(self) -> Health:
        """Quick liveness check. Sub-second. Used by the admin dashboard."""

    def reload(self) -> Result:
        """Best-effort: tell the service to reload after content/config
        changes. Default no-op; override for services that need a SIGHUP
        or an admin-API call."""
        return Result.success("reload not required")
