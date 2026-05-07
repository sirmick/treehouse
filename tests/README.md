# Tests

Two suites, plus the schemas tests under `schemas/`:

| Suite | Needs | Speed | What it covers |
|---|---|---|---|
| `schemas/test_schemas.py` | python + pydantic | <1s | manifest.yml / kids.yml round-trips |
| `tests/test_compose.py` | docker | ~30s | kiwix container behaviour, runs anywhere |
| `tests/test_live.py` | running VM at 10.10.10.1 | ~5s | Caddy host-routing, dnsmasq sinkhole, end-to-end |

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r tests/requirements.txt
```

## Run

```sh
make test               # schemas + compose suites
make test-compose       # compose suite only
make test-live          # live-stack suite (after `make up`)
make test-all           # everything
```

Or directly:

```sh
pytest schemas/                                      # schemas only
pytest tests/test_compose.py                         # compose suite
TREEHOUSE_HOST=10.10.10.1 pytest tests/test_live.py  # live suite
```

## Conventions

- The compose fixture in `tests/conftest.py` brings up
  `compose.test.yml` (kiwix bound to 127.0.0.1:18080), waits for
  health, yields the base URL, then tears down. Project name is
  `treehouse-test` so it's scoped away from any real deployment.

- ZIM-dependent tests (`test_kiwix_lists_loaded_zim`, etc.) skip
  automatically until a `*.zim` file is dropped into
  `tests/fixtures/zims/`. To opt in for richer testing:

  ```sh
  curl -L -o tests/fixtures/zims/wikipedia_en_simple_all_nopic.zim \
       https://download.kiwix.org/zim/wikipedia/wikipedia_en_simple_all_nopic.zim
  ```

  (~1 GB. Don't commit it — `.gitignore` excludes `*.zim`.)

- Live tests use explicit `Host:` headers so they don't depend on
  systemd-resolved being configured on the laptop. They skip if
  10.10.10.1:80 is unreachable (i.e., the VM isn't up).

## When tests should fail

- Compose suite passes ⇒ kiwix container, healthcheck, library.xml
  parsing are all fine. A failure here points at something
  container-shaped (image tag, volume mount, kiwix args).

- Live suite passes ⇒ Caddy host-routing, dnsmasq wildcards, and
  the sinkhole are all wired correctly. A failure here points at
  the Ansible network/proxy roles.

- Both green ⇒ milestone 1's HTTP-layer goals are met. The
  remaining gates (`make verify-isolation`, `make restore-drill`)
  cover the network-layer and backup goals.
