"""Provisioner — reconciles every backend's user state to kids.yml.

Loads kids.yml, walks the adapter registry, calls users_ensure(kids)
on each. Adapters that are unhealthy at the start of a run are skipped
(logged, not fatal — a sick Synapse shouldn't block re-provisioning
Kolibri).

The same orchestration logic powers:
  - manual `make provision-kids` runs
  - the systemd path-watch trigger when kids.yml changes
  - the admin UI's "save" button when it edits kids.yml

Idempotent. Re-running is a no-op when the file hasn't changed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from schemas.kids import KidsConfig

from ..adapters import Adapter, HealthStatus, default_adapters

log = logging.getLogger("provisioner")


@dataclass(frozen=True)
class ProvisionReport:
    healthy_adapters: list[str]
    skipped_unhealthy: list[str]
    successes: dict[str, str]
    failures: dict[str, str]

    @property
    def ok(self) -> bool:
        return not self.failures and not self.skipped_unhealthy

    def render(self) -> str:
        lines = ["# Provisioner report", ""]
        if self.successes:
            lines.append("## ✓ ok")
            for name, summary in self.successes.items():
                lines.append(f"- {name}: {summary}")
            lines.append("")
        if self.skipped_unhealthy:
            lines.append("## ⏭ skipped (unhealthy)")
            for name in self.skipped_unhealthy:
                lines.append(f"- {name}")
            lines.append("")
        if self.failures:
            lines.append("## ✗ failed")
            for name, summary in self.failures.items():
                lines.append(f"- {name}: {summary}")
            lines.append("")
        return "\n".join(lines)


def load_kids(path: Path) -> KidsConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    return KidsConfig.model_validate(raw)


def provision(
    kids_yaml: Path,
    adapters: Iterable[Adapter] | None = None,
    dry_run: bool = False,
) -> ProvisionReport:
    """Top-level orchestration. Adapters injectable for tests."""
    if adapters is None:
        adapters = default_adapters()
    adapters = list(adapters)

    cfg = load_kids(kids_yaml)
    log.info("loaded %d kids from %s", len(cfg.kids), kids_yaml)

    healthy: list[Adapter] = []
    skipped: list[str] = []
    for adapter in adapters:
        h = adapter.health()
        if h.status is HealthStatus.OK:
            healthy.append(adapter)
            log.info("[%s] healthy: %s", adapter.name, h.summary)
        else:
            skipped.append(adapter.name)
            log.warning("[%s] %s: %s", adapter.name, h.status.value, h.summary)

    successes: dict[str, str] = {}
    failures: dict[str, str] = {}

    if dry_run:
        for adapter in healthy:
            successes[adapter.name] = (
                f"would reconcile {len(cfg.kids)} kids (dry-run)"
            )
        return ProvisionReport(
            healthy_adapters=[a.name for a in healthy],
            skipped_unhealthy=skipped,
            successes=successes,
            failures=failures,
        )

    for adapter in healthy:
        result = adapter.users_ensure(cfg.kids)
        if result.ok:
            successes[adapter.name] = result.summary
        else:
            failures[adapter.name] = result.summary

    return ProvisionReport(
        healthy_adapters=[a.name for a in healthy],
        skipped_unhealthy=skipped,
        successes=successes,
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile backend user state to kids.yml."
    )
    parser.add_argument(
        "kids_yaml",
        type=Path,
        nargs="?",
        default=Path("kids.yml"),
        help="Path to kids.yml (default: ./kids.yml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read kids.yml and call health(); skip users_ensure.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    if not args.kids_yaml.exists():
        log.error("kids.yml not found: %s", args.kids_yaml)
        return 2

    report = provision(args.kids_yaml, dry_run=args.dry_run)
    sys.stdout.write(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
