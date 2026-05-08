"""
Shared pytest fixtures.

- `compose_stack` (session-scoped): brings up `compose.test.yml`
  (kiwix only), waits for healthy, yields the kiwix base URL. Used
  by tests/test_compose.py and tests/test_adapters.py.

- `live_base`: returns the netloc of a running treehouse VM if
  reachable, else skips. Used by tests/test_live.py.

Host-routing tests previously lived here (test_compose_proxy.py)
against a compose-side proxy. That proxy was eliminated when host
nginx started talking to backends directly; routing tests now live
in test_live.py against the deployed VM.
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

    try:
        _wait_for_http(f"{kiwix}/", timeout=90)
    except TimeoutError as e:
        logs = subprocess.run(
            [*dc, "-p", PROJECT, "-f", str(COMPOSE_FILE), "logs"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        pytest.fail(f"{e}\n\n--- compose logs ---\n{logs.stdout}\n{logs.stderr}")

    yield {"kiwix": kiwix}

    subprocess.run(
        [*dc, "-p", PROJECT, "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        cwd=REPO_ROOT, check=False, capture_output=True,
    )


@pytest.fixture(scope="session")
def compose_stack(_stack_up: dict[str, str]) -> str:
    """Direct kiwix base URL."""
    return _stack_up["kiwix"]


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
