"""Kiwix adapter.

Kiwix has no user model — every visitor sees the same library. Most
identity verbs are therefore no-ops. The interesting verbs are health
(catalog reachable?) and reload (SIGHUP after the updater swaps a ZIM).
"""

from __future__ import annotations

import os
import subprocess
from typing import Iterable
from urllib.parse import urljoin

import requests

from schemas.kids import Kid

from .base import Adapter, Health, HealthStatus, Result, Token


class KiwixAdapter(Adapter):
    name = "kiwix"
    requires: list[str] = []

    def __init__(
        self,
        base_url: str | None = None,
        container_name: str = "kiwix",
        timeout: float = 5.0,
    ) -> None:
        # Default points at the in-VM kiwix container's host-bound port.
        # Tests inject the compose.test.yml URL.
        self.base_url = base_url or os.environ.get(
            "KIWIX_URL", "http://127.0.0.1:8080"
        )
        self.container_name = container_name
        self.timeout = timeout

    # ----- identity (all no-ops; Kiwix has no users) -----------------------

    def users_ensure(self, kids: Iterable[Kid]) -> Result:  # noqa: ARG002
        return Result.success("kiwix has no user model; nothing to do")

    def users_remove(self, kid_id: str) -> Result:  # noqa: ARG002
        return Result.success("kiwix has no user model; nothing to do")

    def auth_token(self, kid_id: str) -> Token | None:  # noqa: ARG002
        return None

    # ----- ops -------------------------------------------------------------

    def health(self) -> Health:
        try:
            r = requests.get(
                urljoin(self.base_url, "/catalog/v2/entries"),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            return Health(
                status=HealthStatus.UNREACHABLE,
                summary=f"kiwix unreachable: {e.__class__.__name__}",
                details={"error": str(e), "url": self.base_url},
            )

        if r.status_code != 200:
            return Health(
                status=HealthStatus.DEGRADED,
                summary=f"kiwix returned HTTP {r.status_code}",
                details={"status_code": r.status_code},
            )

        # Count <entry> tags in the OPDS feed — gives a useful one-liner.
        entry_count = r.content.count(b"<entry")
        return Health(
            status=HealthStatus.OK,
            summary=f"{entry_count} ZIM(s) loaded",
            details={"entries": entry_count},
        )

    def reload(self) -> Result:
        """SIGHUP the kiwix container so it re-reads library.xml. Run
        after the updater swaps in a new ZIM."""
        try:
            subprocess.run(
                ["docker", "kill", "-s", "HUP", self.container_name],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except FileNotFoundError:
            return Result.failure("docker CLI not available")
        except subprocess.CalledProcessError as e:
            return Result.failure(
                "docker kill failed",
                stderr=e.stderr.decode("utf-8", "replace") if e.stderr else "",
            )
        return Result.success(f"sent SIGHUP to {self.container_name}")
