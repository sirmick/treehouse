"""Provisioner tests with stub adapters.

Stubs let us assert the orchestration semantics — every kid sent to
every adapter, unhealthy adapters skipped not failed, dry-run doesn't
mutate, idempotent re-runs — without standing up real Kolibri/Synapse/etc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest
import yaml

from schemas.kids import Kid
from treehouse.adapters.base import Adapter, Health, HealthStatus, Result, Token
from treehouse.provisioner import provision

REPO_ROOT = Path(__file__).resolve().parent.parent


class StubAdapter(Adapter):
    def __init__(
        self,
        name: str,
        healthy: bool = True,
        ensure_fails: bool = False,
    ) -> None:
        self.name = name
        self._healthy = healthy
        self._ensure_fails = ensure_fails
        self.ensured_kids: list[list[Kid]] = []
        self.removed_ids: list[str] = []
        self.health_calls = 0

    def users_ensure(self, kids: Iterable[Kid]) -> Result:
        kids = list(kids)
        self.ensured_kids.append(kids)
        if self._ensure_fails:
            return Result.failure("simulated failure")
        return Result.success(f"reconciled {len(kids)} kids")

    def users_remove(self, kid_id: str) -> Result:
        self.removed_ids.append(kid_id)
        return Result.success(f"removed {kid_id}")

    def auth_token(self, kid_id: str) -> Token | None:  # noqa: ARG002
        return None

    def health(self) -> Health:
        self.health_calls += 1
        return Health(
            status=HealthStatus.OK if self._healthy else HealthStatus.UNREACHABLE,
            summary="stub" if self._healthy else "stub down",
        )


@pytest.fixture
def kids_yaml_path(tmp_path: Path) -> Path:
    """Use the example kids.yml as the test fixture."""
    src = REPO_ROOT / "kids.yml.example"
    dst = tmp_path / "kids.yml"
    dst.write_bytes(src.read_bytes())
    return dst


# ----- happy path ----------------------------------------------------------

def test_every_healthy_adapter_gets_every_kid(kids_yaml_path: Path) -> None:
    a = StubAdapter("alpha")
    b = StubAdapter("beta")

    report = provision(kids_yaml_path, adapters=[a, b])

    assert report.ok
    assert set(report.healthy_adapters) == {"alpha", "beta"}
    assert report.skipped_unhealthy == []
    assert len(a.ensured_kids[0]) == 3       # alice + bob + guest
    assert len(b.ensured_kids[0]) == 3
    assert {k.id for k in a.ensured_kids[0]} == {"alice", "bob", "guest"}


def test_idempotent_rerun(kids_yaml_path: Path) -> None:
    a = StubAdapter("alpha")
    provision(kids_yaml_path, adapters=[a])
    provision(kids_yaml_path, adapters=[a])
    # Each run sees its own users_ensure call; the adapter's job is to
    # make those calls idempotent inside itself. The orchestrator just
    # invokes consistently.
    assert len(a.ensured_kids) == 2
    assert a.ensured_kids[0] == a.ensured_kids[1]


# ----- failure / degraded paths --------------------------------------------

def test_unhealthy_adapter_skipped_not_failed(kids_yaml_path: Path) -> None:
    healthy = StubAdapter("alpha")
    sick = StubAdapter("beta", healthy=False)

    report = provision(kids_yaml_path, adapters=[healthy, sick])

    assert "alpha" in report.successes
    assert "beta" in report.skipped_unhealthy
    # Sick adapter was never asked to ensure users.
    assert sick.ensured_kids == []
    # ok is False because something was skipped — operator must notice.
    assert not report.ok


def test_failure_recorded_separately_from_skipped(kids_yaml_path: Path) -> None:
    a = StubAdapter("alpha")
    b = StubAdapter("beta", ensure_fails=True)

    report = provision(kids_yaml_path, adapters=[a, b])

    assert "alpha" in report.successes
    assert "beta" in report.failures
    assert report.skipped_unhealthy == []
    assert not report.ok


def test_dry_run_does_not_mutate(kids_yaml_path: Path) -> None:
    a = StubAdapter("alpha")
    report = provision(kids_yaml_path, adapters=[a], dry_run=True)
    assert report.ok
    assert "alpha" in report.successes
    assert "would reconcile" in report.successes["alpha"]
    # users_ensure was NOT called.
    assert a.ensured_kids == []
    # health WAS called (the dry-run still gates on health).
    assert a.health_calls == 1


# ----- report rendering ----------------------------------------------------

def test_report_render_includes_status_sections(kids_yaml_path: Path) -> None:
    healthy = StubAdapter("alpha")
    sick = StubAdapter("beta", healthy=False)
    failing = StubAdapter("gamma", ensure_fails=True)

    report = provision(kids_yaml_path, adapters=[healthy, sick, failing])
    rendered = report.render()

    assert "✓ ok" in rendered
    assert "skipped" in rendered
    assert "failed" in rendered
    assert "alpha" in rendered
    assert "beta" in rendered
    assert "gamma" in rendered
