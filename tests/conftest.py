"""
Shared pytest fixtures.

- `compose_stack` (session-scoped): brings up `compose.test.yml` (caddy +
  kiwix), waits for healthy, yields the kiwix-direct base URL. Used by
  the kiwix-specific tests in test_compose.py.

- `caddy_base` (session-scoped): same stack, yields the caddy base URL.
  Used by the host-routing tests in test_compose_caddy.py — these
  validate the same logic as test_live.py without needing the VM.

- `live_base`: returns the IP of a running treehouse VM if reachable,
  else skips. Used by tests/test_live.py.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "compose.test.yml"
PROJECT = "treehouse-test"
KIWIX_PORT = 18180
PROXY_PORT = 18181


def _docker_compose() -> list[str]:
    """Return the right invocation for docker compose on this host."""
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return ["docker", "compose"]
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        pytest.skip("docker compose plugin not available")
        return []


def _wait_for_http(url: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code < 500:
                return
        except requests.RequestException as e:
            last_err = e
        time.sleep(1)
    raise TimeoutError(f"{url} did not become healthy within {timeout}s ({last_err})")


@pytest.fixture(scope="session")
def _stack_up() -> Iterator[dict[str, str]]:
    """Bring the full compose.test.yml up once per session."""
    dc = _docker_compose()

    subprocess.run(
        [*dc, "-p", PROJECT, "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        cwd=REPO_ROOT, check=False, capture_output=True,
    )

    up = subprocess.run(
        [*dc, "-p", PROJECT, "-f", str(COMPOSE_FILE), "up", "-d"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if up.returncode != 0:
        pytest.fail(f"docker compose up failed:\n{up.stdout}\n{up.stderr}")

    kiwix = f"http://127.0.0.1:{KIWIX_PORT}"
    proxy = f"http://127.0.0.1:{PROXY_PORT}"

    try:
        _wait_for_http(f"{kiwix}/", timeout=90)
        # Proxy depends_on:service_healthy, so it should be up by now,
        # but a quick probe confirms it.
        _wait_for_http(proxy, timeout=30)
    except TimeoutError as e:
        logs = subprocess.run(
            [*dc, "-p", PROJECT, "-f", str(COMPOSE_FILE), "logs"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        pytest.fail(f"{e}\n\n--- compose logs ---\n{logs.stdout}\n{logs.stderr}")

    yield {"kiwix": kiwix, "proxy": proxy}

    subprocess.run(
        [*dc, "-p", PROJECT, "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        cwd=REPO_ROOT, check=False, capture_output=True,
    )


@pytest.fixture(scope="session")
def compose_stack(_stack_up: dict[str, str]) -> str:
    """Direct kiwix base URL — for tests that hit kiwix without caddy."""
    return _stack_up["kiwix"]


@pytest.fixture(scope="session")
def proxy_base(_stack_up: dict[str, str]) -> str:
    """Proxy (nginx/caddy) base URL — for tests that validate
    host-header routing. Technology-agnostic name so swapping the
    proxy doesn't churn test code."""
    return _stack_up["proxy"]


@pytest.fixture(scope="session")
def live_base() -> str:
    """Netloc for live-stack tests against the deployed VM.

    Default 10.10.10.1. Override with TREEHOUSE_HOST=<host[:port]> —
    `make test-live` sets 127.0.0.1:18080 (an SSH local-forward into
    the VM, since the host has no route to br-kids). Skip if
    unreachable.
    """
    netloc = os.environ.get("TREEHOUSE_HOST", "10.10.10.1")
    if ":" in netloc:
        host, port_str = netloc.rsplit(":", 1)
        port = int(port_str)
    else:
        host, port = netloc, 80
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except OSError:
        pytest.skip(f"no route to {netloc} — bring up the VM first")
    return netloc
