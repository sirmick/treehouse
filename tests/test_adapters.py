"""Adapter contract tests.

- Contract: every concrete adapter must implement the 5 verbs and
  produce typed return values.
- Integration: KiwixAdapter against the compose.test.yml fixture.
"""

from __future__ import annotations

from typing import Iterable

import pytest

from schemas.kids import Kid
from treehouse.adapters import (
    Adapter,
    Health,
    HealthStatus,
    KiwixAdapter,
    Result,
    Token,
    default_adapters,
)


# ----- contract -------------------------------------------------------------

def _sample_kid() -> Kid:
    return Kid.model_validate({
        "id": "alice",
        "name": "Alice",
        "age": 8,
        "age_band": "6-10",
        "avatar": "avatars/alice.png",
        "pin": "1234",
    })


@pytest.mark.parametrize("adapter", default_adapters(), ids=lambda a: a.name)
def test_adapter_implements_contract(adapter: Adapter) -> None:
    assert adapter.name, "adapter.name must be non-empty"
    assert isinstance(adapter.requires, list)

    # Returns must be typed correctly even on no-op adapters.
    r = adapter.users_ensure([_sample_kid()])
    assert isinstance(r, Result)

    r = adapter.users_remove("alice")
    assert isinstance(r, Result)

    tok = adapter.auth_token("alice")
    assert tok is None or isinstance(tok, Token)

    h = adapter.health()
    assert isinstance(h, Health)
    assert h.status in HealthStatus


# ----- KiwixAdapter integration --------------------------------------------

def test_kiwix_health_against_compose(compose_stack: str) -> None:
    adapter = KiwixAdapter(base_url=compose_stack)
    h = adapter.health()
    assert h.status is HealthStatus.OK, h
    # Empty library so 0 entries; the summary still mentions the count.
    assert "ZIM" in h.summary


def test_kiwix_health_unreachable() -> None:
    adapter = KiwixAdapter(base_url="http://127.0.0.1:1", timeout=1.0)
    h = adapter.health()
    assert h.status is HealthStatus.UNREACHABLE


def test_kiwix_users_ensure_is_noop(compose_stack: str) -> None:
    adapter = KiwixAdapter(base_url=compose_stack)
    r = adapter.users_ensure([_sample_kid(), _sample_kid()])
    assert r.ok
    assert "no user model" in r.summary


def test_kiwix_auth_token_returns_none() -> None:
    adapter = KiwixAdapter()
    assert adapter.auth_token("alice") is None
