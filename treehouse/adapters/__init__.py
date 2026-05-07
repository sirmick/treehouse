"""Adapter registry. Adding a new service later means: implement Adapter,
import it here, append to default_adapters(). The provisioner, the broker,
and the admin dashboard all read from this registry."""

from __future__ import annotations

from .base import Adapter, Cookie, Health, HealthStatus, Result, Token
from .kiwix import KiwixAdapter


def default_adapters() -> list[Adapter]:
    """Factory used by the provisioner, broker, and admin dashboard.
    Construction is lazy so tests can inject stubs without importing
    every concrete adapter."""
    return [
        KiwixAdapter(),
    ]


__all__ = [
    "Adapter",
    "Cookie",
    "Health",
    "HealthStatus",
    "KiwixAdapter",
    "Result",
    "Token",
    "default_adapters",
]
