# Identity

How a kid is recognized by Treehouse, how each backend service learns
about her, and how she logs into everything by picking an avatar.

The system is **vaulted SSO**, not federated SSO. The launcher is the
identity authority; each backend has its own native account model; the
launcher knows each kid's credentials in each backend and brokers
logins on her behalf. From the kid's perspective it's seamless single
sign-on. From the architecture's perspective it's a credential vault
with a redirect-broker. See [the SSO classification section](#sso-classification)
for the precise framing.

## `kids.yml` — the user-facing surface

A single YAML file at the repo root, version-controlled. Mick edits
it; the provisioner reconciles every backend to match.

```yaml
kids:
  - id: alice                       # stable, never changes
    name: Alice
    age: 8
    avatar: avatars/alice.png
    pin: "1234"                     # null for no-pin (dedicated device)
    age_band: "6-10"
    permissions:
      messaging: true
      messaging_contacts: [bob, mum, dad, grandma]
      videos_curfew_after: "19:00"
      activities: all               # or list of activity IDs
      ai_companion: true

  - id: bob
    name: Bob
    age: 5
    avatar: avatars/bob.png
    pin: null
    age_band: "3-7"
    permissions:
      messaging: false
      activities: [TurtleArt, Memorize, Maze, Physics]
      ai_companion: true

  - id: guest                       # special-purpose
    name: Visitor
    age: 0                          # used only for age-band defaults
    avatar: avatars/guest.png
    pin: null
    ephemeral: true                 # session state wiped on logout
    permissions:
      messaging: false
      activities: [Memorize, Maze, Physics, Speak]
      ai_companion: false

contacts:
  # Adults / family who appear in messaging contact lists.
  # No PIN; their tile only appears in admin views.
  - id: mum
    name: Mum
    avatar: avatars/mum.png
    role: parent
  - id: dad
    name: Dad
    avatar: avatars/dad.png
    role: parent
  - id: grandma
    name: Grandma
    avatar: avatars/grandma.png
    role: family
```

The `id` field is the stable primary key. **Names and avatars can
change; ids cannot.** The system uses `id` as the literal username in
every backend service:

- Kolibri username: `alice`
- Matrix MXID: `@alice:kids`
- PeerTube username: `alice`
- Sugarizer user: `alice`

This is the design choice that makes recovery cheap (see below).

## SSO classification

What we have is **credential-vaulted SSO**, sometimes called
"broker-based" or "password-vault" SSO.

| | Federated SSO (OIDC) | What Treehouse does |
|---|---|---|
| Source of truth for credentials | The IdP only; services hold no passwords | Each service has its own account; launcher caches credentials |
| Trust model | Services trust IdP-signed assertions | Services do their normal native login; launcher does it on the kid's behalf |
| Logout-everywhere | Native, single call to IdP | Best-effort: launcher loops through services revoking sessions |
| Adding a new service | Free *if* it speaks OIDC; impossible if it doesn't | Always possible — write an adapter |
| Audit chain | "Alice authenticated to IdP at T" | Each service sees "alice logged in normally"; brokering is invisible |

The decision to vault rather than federate is driven by the service
set: **Kolibri, Sugarizer, and Calibre-web have no native OIDC
support.** Implementing federated SSO would require writing OIDC
bridges for each, which is weeks of work for one family's needs. The
properties that actually matter — one entry point, central admin,
per-kid permissions, unified identity — are all preserved by vaulting.

## Adapter contract

Every backend service has an adapter implementing eight verbs:

```python
# treehouse/adapters/base.py
class Adapter(ABC):
    name: str
    requires: list[str] = []     # other adapters that must be healthy first

    # content lifecycle (covered in content.md)
    def content_plan(self, manifest)  -> list[Action]: return []
    def content_apply(self, plan)     -> Result: ...
    def content_prune(self, manifest) -> Result: ...

    # identity lifecycle
    def users_ensure(self, kids)      -> Result: ...
    def users_remove(self, kid_id)    -> Result: ...
    def auth_token(self, kid_id)      -> Token | None: ...

    # ops
    def health(self) -> HealthStatus: ...
    def reload(self) -> None: pass
```

`users_ensure` and `users_remove` are idempotent. `auth_token` mints
or returns a session usable to log this kid into the service.
Adapters that don't have users (Kiwix, tileserver) implement no-ops
for those verbs.

## Per-service implementation

### Kolibri

- **Identity model:** Kolibri's "facility users" tied to a "facility"
  entity. Token-based session auth.
- **Provision** (`users_ensure`): for each kid, ensure a facility
  exists (default: `Treehouse`); ensure the user exists with
  username = `kid.id`, full_name = `kid.name`, password = the cached
  password from `state/launcher/credentials.sqlite`. POST
  `/api/auth/facilityuser/`.
- **Mint session** (`auth_token`): POST
  `/api/auth/session/` with username/password. Returns a `sessionid`
  cookie. Hand this to the kid's browser via a 302 with `Set-Cookie`.
- **Remove** (`users_remove`): PATCH `is_active=false`. Don't hard
  delete; preserves their progress for re-enable.

### Synapse (Matrix)

- **Identity model:** users keyed by MXID (`@alice:kids`); access
  tokens for sessions; password optional (can mint tokens directly
  via admin API).
- **Provision:** PUT `/_synapse/admin/v2/users/@<id>:kids` with
  `displayname` and `password`. Idempotent (PUT semantics —
  re-applying yields the same state).
- **Mint session:** POST `/_synapse/admin/v1/users/<mxid>/login` →
  returns an access token usable for any client. Long-lived; revocable.
- **Hand off to Element:** the launcher serves an intermediate page
  that writes `mx_access_token`, `mx_user_id`, `mx_device_id`,
  `mx_hs_url` into `localStorage`, then redirects to Element.
- **Encryption:** rooms unencrypted by LAN-only design. Disabling
  E2EE avoids the device-key churn that vaulted token rotation would
  otherwise cause.
- **Remove:** PUT with `deactivated=true`.

### PeerTube

- **Identity model:** OAuth2 password-grant; user accounts.
- **Provision:** POST `/api/v1/users` with admin OAuth token in
  `Authorization` header. Username = `kid.id`.
- **Mint session:** POST `/api/v1/users/token` with grant_type=password
  → access token + refresh token. Set as cookies on the redirect.
- **Federation:** off (`federation_videos_federate_unlisted: false`,
  no follows). PeerTube is a single-tenant local instance.
- **Remove:** DELETE `/api/v1/users/<username>`.

### Sugarizer

- **Identity model:** Sugarizer's own user model with bearer tokens.
- **Provision:** POST `/api/v1/users` with admin token. Username =
  `kid.id`. Sugarizer assigns a colour and a name, both overridable.
- **Mint session:** POST `/auth/login` → bearer token. Sugarizer
  expects this in localStorage.
- **Remove:** DELETE `/api/v1/users/<id>`.

### Calibre-web

- **Identity model:** simple users table in calibre-web's sqlite. No
  rich admin API.
- **Provision:** direct sqlite insert (idempotent on `username`
  unique constraint), or use calibre-web's `/admin/user` POST flow.
  Either works; sqlite is more reliable.
- **Mint session:** HTTP Basic auth — the launcher injects an
  `Authorization` header into the redirect. No real session needed.
- **Remove:** delete the row.

### Kiwix and tileserver

- No users. All identity verbs no-op.

## The provisioner

`provisioner` is a one-shot script (CLI + systemd unit) that
reconciles every backend to `kids.yml`.

```python
def provision():
    kids = load_kids_yaml()
    adapters = load_adapters()

    for adapter in topo_sort(adapters):
        if not adapter.health():
            log.warning(f"skipping {adapter.name}, unhealthy")
            continue

        # Add or update active kids
        for kid in kids:
            adapter.users_ensure(kid)

        # Disable kids removed from kids.yml
        for removed_id in deleted_kids():
            adapter.users_remove(removed_id)

    rebuild_credentials_cache(kids)
    write_provision_report()
```

It's safe to run repeatedly — every operation is idempotent, so the
same `kids.yml` produces the same backend state regardless of starting
point.

The provisioner runs in three situations:

1. **Manually**, after editing `kids.yml`. (`make provision-kids`.)
2. **Automatically**, on the systemd path-watch unit that fires when
   `kids.yml` is modified.
3. **As part of the deploy**, after Ansible brings up new services.

## The credentials cache

`state/launcher/credentials.sqlite`:

```sql
CREATE TABLE credentials (
    kid_id TEXT NOT NULL,
    service TEXT NOT NULL,        -- 'kolibri', 'synapse', etc
    secret_kind TEXT NOT NULL,    -- 'password', 'access_token', 'oauth'
    secret_value TEXT NOT NULL,   -- encrypted at rest
    rotated_at TIMESTAMP,
    PRIMARY KEY (kid_id, service)
);
```

The launcher reads this when minting sessions. The provisioner writes
to it whenever it generates a new password or token.

**This file is a cache, not source of truth.** The provisioner can
recreate it from `kids.yml` plus the per-service admin tokens (which
live in `state/launcher/secrets.yml`). Procedure to rebuild:

```
make rotate-kid-creds
```

This runs the provisioner with `--reset-passwords`, which:

1. For each kid × service, generates a fresh password/token.
2. Calls each adapter's password-set endpoint (Kolibri's
   `set_password`, Synapse's PUT user, etc).
3. Repopulates `credentials.sqlite`.

Existing kid *data* in each backend stays intact because the username
matched. So even total loss of `credentials.sqlite` is a fast recovery,
not a disaster.

## Login broker — the runtime flow

```
Kid hits home.kids
  └─ launcher renders avatar grid

Kid taps Alice's tile, enters PIN
  └─ POST /api/login {kid_id: alice, pin: 1234}
      ├─ launcher validates PIN against kids.yml
      ├─ sets signed `kidsession` cookie scoped to .kids
      └─ returns 302 → /home

Kid taps "Khan Academy"
  └─ GET /api/launch/kolibri (kidsession cookie present)
      ├─ launcher reads kidsession → kid_id=alice
      ├─ check permissions: alice has activity-band access
      ├─ check curfew: ok
      ├─ adapter.kolibri.auth_token(alice) →
      │    POST kolibri:8080/api/auth/session/
      │       {username: alice, password: <from sqlite>}
      │    response: Set-Cookie: sessionid=...
      ├─ launcher returns:
      │    HTTP/1.1 302 Found
      │    Location: https://khan.kids
      │    Set-Cookie: sessionid=...; Domain=khan.kids; ...
      └─ kid's browser follows redirect, arrives logged in
```

The redirect is the entire login flow. The kid never sees a Kolibri
login screen.

## The Caddy redirect — closing the bypass

A kid with a stored bookmark to `khan.kids` would skip the launcher
entirely and see Kolibri's native login. To prevent this, Caddy
redirects any service-host request lacking `kidsession` back through
the launcher:

```caddyfile
@no_session not header_regexp Cookie kidsession=

khan.kids, play.kids, books.kids, videos.kids, chat.kids {
    redir @no_session https://home.kids/launch?to={host} 302
    reverse_proxy <backend>:<port>
}
```

After the launcher's `/launch?to=khan.kids` flow runs, the kid arrives
at `khan.kids` *with* a `kidsession` cookie *and* a Kolibri sessionid
cookie, the redirect doesn't fire, and the proxy passes through.

This makes the broker unbypassable from a normal browser. The only way
to skip the launcher would be to have a Kolibri sessionid cookie in
hand, which only the launcher mints.

## Permission enforcement

Permissions live in `kids.yml.kids[].permissions` and are enforced
at the launcher in two places:

1. **At launch time:** `/api/launch/<service>` checks whether this
   kid is allowed to use this service before minting a session. If
   `permissions.messaging: false`, `chat.kids` redirects refuse with
   a friendly message.
2. **At the tile renderer:** the home tiles for services the kid
   can't access aren't rendered. (Tiles for in-curfew services are
   greyed out with a "back at 7" tooltip.)

Centralizing policy at the launcher means each backend stays vanilla —
no per-service policy code. Adding a new policy ("max 30 min/day on
videos") is a launcher feature, not a multi-service rollout.

## Switch user, sign out, idle timeout

- **Switch user:** persistent button in the launcher chrome. Calls
  `/api/logout`, which invalidates `kidsession`, then walks each
  adapter's logout endpoint (best-effort; failures non-fatal),
  redirects to avatar grid.
- **Sign out:** same as switch user from the kid's perspective.
- **Idle timeout:** the launcher's session has a configurable max
  age (default 30 min of no activity). On expiry: same as logout.
  This matters for shared tablets — sibling picks up after, gets her
  own login.

## Admin identity

A separate path: `admin.kids` runs through Caddy with HTTP Basic
auth, gated by Mick's password (stored hashed in Caddy's config).
Mick's identity is not in `kids.yml` — it's a different layer entirely.

The admin pages let Mick:

- See per-adapter health
- View the latest update report
- See per-kid AI conversation logs
- Edit `kids.yml` and `manifest.yml` (with git commit on save)
- Trigger manual provisioner / updater runs

## Bootstrap and secrets

At install time, Ansible generates random admin tokens/passwords for
each backend (Synapse `registration_shared_secret`, PeerTube admin
password, Kolibri facility admin, etc). These go to two places:

1. The service's own config (Synapse `homeserver.yaml`, etc).
2. `state/launcher/secrets.yml` — read by the provisioner and the
   launcher.

`secrets.yml` is mode 0600, owned by the launcher service user.
Optionally encrypted with `sops` so it can be committed to git;
otherwise generated fresh per deploy and snapshotted via restic.

The provisioner only ever runs on-box, talking to localhost. No
admin endpoints are exposed externally — Caddy 403s on
`/_synapse/admin/*`, `/api/v1/users` POSTs, etc. unless the request
originates from the launcher's container or localhost.

## Open questions

- **Should the launcher support stateless device-bound identity?**
  e.g., a tablet permanently bound to Alice (no avatar select needed).
  Convenient, but couples identity to hardware. Defer until experience
  shows the avatar select is friction.
- **Should there be parental override / supervised access?** Mick
  logging in as Alice to see what she sees. Mostly available now via
  admin views; a "view as Alice" mode would be a small extension.
- **Should we support guests / friends visiting?** The `guest` kid in
  `kids.yml` covers this. Question is whether ephemeral state (no
  Sugarizer journal saving, etc) is the right default. Yes for now.
