#!/usr/bin/env bash
# Run tests/test_live.py against the kids segment.
#
# Single-NIC architecture: the laptop hosting the VM can't reach the
# VM's kids IP directly (macvtap-host isolation in lan mode; isolated
# br-kids has no host-side IP). The earlier SSH-local-forward path
# went via the mgmt NIC, which no longer exists.
#
# Three remaining options for actually running this:
#
# 1. From any other device on the LAN (a phone in `vm` mode, your
#    other laptop, the proxy box) — set TREEHOUSE_HOST to the VM's
#    LAN IP and run `python -m pytest tests/test_live.py` there.
#
# 2. From inside the VM via qemu-ga — invoking pytest VM-side is a
#    follow-up slice (needs pytest + requests installed there).
#
# 3. Through the public path — use the homezone alias from
#    treehouse.yml (TREEHOUSE_HOST=<public-name>:443 with a small
#    change to test_live.py to use https://). Slow because it goes
#    out to the internet and back.

set -euo pipefail

cat <<EOF >&2
[test-live] cannot run from this laptop in single-NIC mode.

  Run from any other LAN device:
    TREEHOUSE_HOST=$(./bin/cfg network.lan.ip | cut -d/ -f1) \\
      python -m pytest tests/test_live.py -v

EOF
exit 2
