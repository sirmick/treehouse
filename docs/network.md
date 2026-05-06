# Network

The network is what makes Treehouse safe. The rest of the system is
content and identity; without correct network isolation, all of that is
beside the point. This doc is the precise design of the kids' segment:
what's physically on it, how DHCP and DNS work, how upstream is denied,
and how the experience travels.

## Goals

1. **Structural isolation.** No path from any kids' device to the public
   internet exists. This is enforced at three independent layers
   (routing table, packet filter, AP configuration) so no single
   misconfiguration is fatal.
2. **No surprises.** Every name a kid types resolves to something
   meaningful (a friendly landing page, a service, or a "we don't have
   that" page). Nothing times out into a 30-second blank screen.
3. **Maintainable from one operator.** Mick reads `nftables list ruleset`
   in one sitting and understands the whole thing.

## Topology

```
   ┌────────────────────────────────────────────────────────────┐
   │  KIDS' SEGMENT (10.10.10.0/24)                             │
   │                                                            │
   │   [tablet]  [tablet]  [TV]                                 │
   │      │         │       │                                   │
   │      │ WiFi (SSID: treehouse, WPA2)                        │
   │      ▼                                                     │
   │   ┌─────────────────────────────────────┐                  │
   │   │  WiFi AP                            │                  │
   │   │  - Client isolation: ON             │                  │
   │   │  - WAN port: unplugged / disabled   │                  │
   │   │  - LAN port: bridged to host        │                  │
   │   └─────────┬───────────────────────────┘                  │
   │             │ ethernet                                     │
   │             ▼                                              │
   │   ┌─────────────────────────────────────┐                  │
   │   │  Treehouse host                     │                  │
   │   │  IP: 10.10.10.1                     │                  │
   │   │  Provides: DHCP, DNS, all services  │                  │
   │   │  Default route on this interface:   │                  │
   │   │    *** none ***                     │                  │
   │   └─────────────────────────────────────┘                  │
   │                                                            │
   └────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────┐
   │  MANAGEMENT (sealed off in production)                     │
   │                                                            │
   │   [Mick's workstation] ── ssh ─► host's mgmt iface         │
   │                                  (different physical port  │
   │                                  / different VLAN)         │
   │                                                            │
   │   This interface is only enabled during `make unseal`      │
   │   maintenance windows. Normal operation: down.             │
   └────────────────────────────────────────────────────────────┘
```

The host has two network interfaces:

- **`br-kids` (10.10.10.1/24)** — bridged to the AP's LAN. dnsmasq listens
  here, all services bind here. **No default route on this interface.**
- **management interface** — separate physical port (or VLAN) used only
  for ssh administration. Toggled off via `make seal` for production
  operation.

## Physical AP

For the production family deployment, an external WiFi AP is preferred
over the Pi's onboard radio: better range, separation of concerns,
client isolation built-in.

### Recommended hardware

| Option | Why | Cost |
|---|---|---|
| **GL.iNet Slate AX (GL-AXT1800)** | OpenWrt out of the box, great client-isolation support, USB-C powered, fits in a drawer | ~$120 |
| **GL.iNet Beryl AX** | Same family, smaller, slightly less range | ~$100 |
| **UniFi U6-Lite + USG/UDM-Lite** | Ubiquiti VLANs are clean, but it's a bigger commitment | ~$200+ |
| **Stock router in AP-only mode** | Cheapest; viable if you can disable WAN routing and enable client isolation | varies |

The GL.iNet line is the recommended starting point: OpenWrt is the
software you actually want, and the form factor is friendly.

### OpenWrt configuration on the AP

Three things must be true:

1. **WAN is unplugged or disabled.** No upstream from the AP itself.
2. **Client isolation is on.** Devices on the kids' SSID cannot talk to
   each other directly — only to the host. Prevents one kid's compromised
   tablet (or unmanaged smart toy) from reaching another's.
3. **The LAN port is bridged onto the kids' segment.** Either by
   plugging into a switch port that's also connected to the host's
   `br-kids` interface, or (in a VM dev environment) by attaching the AP
   to a libvirt-managed bridge.

Key UCI excerpts:

```
# /etc/config/wireless
config wifi-iface 'kids'
    option device 'radio0'
    option network 'lan'
    option mode 'ap'
    option ssid 'treehouse'
    option encryption 'psk2'
    option key '<wpa2-pre-shared-key>'
    option isolate '1'                # client isolation
    option ieee80211w '1'

# /etc/config/network
config interface 'wan'
    option proto 'none'               # WAN explicitly disabled

config interface 'lan'
    option type 'bridge'
    option ifname 'eth1'
    option proto 'static'
    option ipaddr '10.10.10.2'        # AP's own IP for management
    option netmask '255.255.255.0'
    option gateway ''                 # *** no gateway ***
    option dns ''                     # rely on dnsmasq for resolution
```

The AP itself does not provide DHCP or DNS — the host does. Disable
dnsmasq on the AP. The AP is purely a layer-2 bridge with WiFi.

## DHCP — dnsmasq on the host

The host's dnsmasq is authoritative for the kids' segment. It hands out
leases, sets the host as the only DNS server, and (crucially) does NOT
set a gateway.

```
# /etc/dnsmasq.d/kids.conf
interface=br-kids
bind-interfaces

# DHCP scope
dhcp-range=10.10.10.50,10.10.10.250,12h
dhcp-option=option:router,                # NO GATEWAY (empty value)
dhcp-option=option:dns-server,10.10.10.1
dhcp-option=option:domain-name,kids
dhcp-option=option:ntp-server,10.10.10.1

# DNS
domain=kids
local=/kids/
expand-hosts

# Wildcard *.kids → host
address=/.kids/10.10.10.1

# Sinkhole everything else (no upstream resolution)
address=/#/10.10.10.1

# Don't use any upstream resolver
no-resolv
no-poll
```

The `address=/#/10.10.10.1` line is the most important non-obvious bit:
**any DNS query that doesn't match `.kids` resolves to the host's IP**.
This means a kid typing `youtube.com` doesn't get a 30-second timeout
or a confusing "connection refused" — they get Caddy's "Hmm, that's not
something we have" page. (Caddy's default vhost handles this; see the
`proxy` role.)

## DNS — split between served names and sinkhole

Two response classes:

- **Real services**: `home.kids`, `wikipedia.kids`, `khan.kids`,
  `videos.kids`, `chat.kids`, `books.kids`, `maps.kids`,
  `play.kids`, `admin.kids`, etc. These all resolve to `10.10.10.1`
  via the wildcard. Caddy distinguishes them by Host header and routes
  to the right backend.
- **Everything else** (`youtube.com`, `tiktok.com`, anything): resolves
  to `10.10.10.1` via the sinkhole. Caddy's default vhost serves a
  friendly redirect page:

  > That's not something we have at home. Try:
  > 🏠 home.kids — your home page
  > 🔍 home.kids/search — search everything

The sinkhole doubles as a useful telemetry surface: log unknown-name
hits, and you'll learn what kids are typing in. Could inform what to
add to the manifest. (Privacy note: keep these logs local-only and
purge after 30 days.)

## nftables — defense in depth

dnsmasq + missing default route is enough to prevent upstream traffic
in normal operation. nftables is the belt-and-suspenders layer for the
case where one of those breaks.

```
# /etc/nftables.d/kids.nft
table inet kids {
    chain forward {
        type filter hook forward priority 0; policy drop;

        # Allow kids segment ↔ host services (already DNAT'd by Caddy)
        iifname "br-kids" oifname "br-kids" accept

        # Explicitly drop kids → anywhere else
        iifname "br-kids" drop
    }

    chain output {
        type filter hook output priority 0; policy accept;
        # The host can talk to whatever; only kids segment is restricted.
    }

    chain input {
        type filter hook input priority 0; policy drop;

        ct state established,related accept
        iifname "lo" accept

        # Kids segment can reach host services on these ports
        iifname "br-kids" tcp dport { 53, 80, 443, 67, 68 } accept
        iifname "br-kids" udp dport { 53, 67, 68, 123 } accept

        # Management interface: ssh only, when up
        iifname "mgmt0" tcp dport 22 accept
    }
}
```

The forward chain is the critical one: `policy drop` plus an explicit
`iifname "br-kids" drop` means even if a default route accidentally
appears on the host, packets from the kids' segment are still discarded
before being forwarded.

The host has its own outbound access (when management is up) for
package updates and content downloads. The drop rule only applies to
*forwarding*, not to the host's own outbound.

## Caddy — the single user-facing port

All inbound from the kids' segment is intermediated by Caddy. Per-vhost
routing fans out to backends; a default vhost catches sinkholed requests.

```caddyfile
{
    # No automatic HTTPS — internal-only, plain HTTP avoids cert warnings.
    auto_https off
}

# Friendly landing for any unknown hostname (sinkholed by dnsmasq)
:80 {
    handle {
        respond "That's not something we have at home." 404
        # Or render a static template with helpful suggestions.
    }
}

home.kids, kids {
    root * /srv/treehouse/launcher
    file_server
    reverse_proxy /api/* launcher:8000
}

wikipedia.kids { reverse_proxy kiwix:8080 }
khan.kids      { reverse_proxy kolibri:8080 }
play.kids      { reverse_proxy sugarizer:8089 }
books.kids     { reverse_proxy calibre:8083 }
videos.kids    { reverse_proxy peertube:9000 }
maps.kids      { reverse_proxy tileserver:8080 }
chat.kids      { reverse_proxy element:80 }

# Service hosts redirect through launcher if no kidsession cookie:
@no_session not header_regexp Cookie kidsession=
khan.kids, play.kids, books.kids, videos.kids, chat.kids {
    redir @no_session https://home.kids/launch?to={host} 302
}

admin.kids {
    basicauth { mick $2a$14$... }
    reverse_proxy launcher:8000/admin
}
```

The broker-redirect on `@no_session` is what makes vaulted SSO
unbypassable: a kid (or curious tablet) hitting `khan.kids` directly
gets bounced through the launcher first, and only ends up at Kolibri
*with a Kolibri session cookie injected by the launcher*.

## NTP without internet

There is no upstream NTP. Two options:

1. **Local stratum-10 chrony.** The host advertises itself as a clock
   source on the kids segment. Time is whatever the RTC says at boot,
   slewed slowly. Acceptable: small drift over weeks doesn't matter for
   anything kid-facing.
2. **GPS time source.** Overkill for a family deployment. Mentioned
   for completeness — a $40 USB GPS module gives you stratum-1 if for
   some reason you need it.

The Pi 5 RTC needs a battery (small CR2032 socket on the board); without
it, every reboot resets to epoch and Postgres has indigestion. **Don't
forget the battery.**

`chrony.conf` snippet:

```
local stratum 10
allow 10.10.10.0/24
makestep 1.0 3
rtcsync
```

## Travel scenarios

When the family travels, two patterns are viable:

### Pattern A — the box travels

A small Pi or GL.iNet AP, battery-powered, in a bag. Same Compose stack
(or a `--profile=travel` subset). Plug into a USB battery; kids connect
to the same SSID they have at home. Works in a car, hotel room,
grandma's house.

Tradeoffs: device must be carried; limited storage; rebuild content for
travel profile.

### Pattern B — the tablet travels

A PWA-installable launcher with cached content (selected ZIMs, a few
videos, a few activities) loaded into the device's storage. No box
needed; just the tablet. Works anywhere with no network at all.

Tradeoffs: device-specific (one tablet at a time); content sync is more
complex (rsync over WiFi when home, then fly).

Pattern A is simpler operationally and is the planned default for
Phase 10. Pattern B is on the wishlist if Pattern A proves
inconvenient.

## Multi-AP / range extension

If one AP doesn't cover the house, add a second GL.iNet on the same
LAN bridge. dnsmasq is still authoritative because it's on the host;
the second AP just bridges layer-2. Don't run dnsmasq on the AP.

For very large coverage areas (multiple floors), UniFi gear with a
dedicated controller is more capable but introduces enough complexity
that it's worth thinking carefully about whether it's needed. Most
family-sized homes do fine with one or two GL.iNets.

## Open questions

- **Should device-MAC-based per-kid identification supplement the
  launcher's avatar selection?** A tablet that's only used by Alice
  could auto-pick her avatar. Convenient, but couples identity to
  hardware in a way that might bite (cloned MACs, lost tablets,
  guest devices). Defer until experience shows it's needed.
- **Should the AP enforce per-device bandwidth caps?** Probably not —
  this is a family-of-few network and contention is unlikely. Mention
  for completeness; revisit if PeerTube transcoding ever bogs down a
  kid's stream.
- **What happens when management interface is up?** Strictly: a route
  to the public internet exists on the host, but not on the kids'
  segment. The nftables forward `drop` rule prevents leakage. Document
  the verification ritual after each `unseal`/`seal` cycle in
  `operations.md`.
