# Launcher

The kid-facing surface and the identity broker. Everything a kid sees,
plus the redirect mechanics that get them logged into each backend.

The launcher is the most-touched component in the system; it earns the
right to be the most carefully designed and the most thoroughly tested.

## Goals

1. **Testable end-to-end.** Every flow a kid can experience must be
   reachable from an automated test. The flagship test asserts:
   "Alice picks her avatar, enters her PIN, taps Khan Academy, and
   ends up on the Kolibri dashboard logged in as alice." If that
   passes, the most-used path through the system is healthy.
2. **Pre-reader friendly.** The launcher is usable by a kid who can't
   read fluently. Big tiles, TTS labels, no URL bar, no text input
   except PIN digits and search.
3. **No surprises.** Predictable, gentle UI. No nagware. No external
   links that ever escape the kids' segment (which is structurally
   impossible anyway, but the UI doesn't even tease).
4. **Separation of view and logic.** Components are render shells.
   All business logic lives in a `LauncherClient` class that's
   unit-testable without a browser.
5. **Deployable as static assets.** The frontend is a SvelteKit
   build (adapter-static) that nginx serves directly from
   `/srv/treehouse/launcher/`.
   The backend is a FastAPI service in a container. Two deployable
   units, simple to reason about.

## Tech stack

| Concern | Choice | Why |
|---|---|---|
| Frontend framework | **SvelteKit + TypeScript** | Less ceremony than React for this scope; file-based routing matches the launcher's mental model; SSR by default for fast first paint on cheap tablets. |
| Backend / broker | **FastAPI (Python)** | Same language as updater, provisioner, searchd, aigateway; one runtime, one set of dependencies. |
| Component testing | **Vitest + @testing-library/svelte** | Fast, parallel, runs in CI in seconds. |
| End-to-end testing | **Playwright** | Multi-browser, traces, screenshots-checked-into-git for visual regression. |
| Backend integration | **pytest + httpx** | Standard Python integration tests against a real `compose.test.yml`. |
| Bundling | **Vite** (built into SvelteKit) | Default. |
| Styling | **Tailwind CSS** | Easy to keep consistent across screens; small generated bundle. |

Explicitly rejected:

- **React.** Heavier, more ceremony, no benefit at this scale.
- **Server-side everything (htmx + Jinja).** The launcher needs more
  interactivity (search results, tile animations, PIN entry feedback)
  than htmx is graceful with. Reasonable for v0.5; SvelteKit is
  better for v1.
- **Phoenix LiveView.** Would add Elixir to the stack. Beautiful
  framework, wrong tradeoff for a one-language house.
- **Native apps.** A PWA suffices. Native is a Phase 11+ concern.

## Screens

```
                            ┌─────────────────┐
                            │  Avatar grid    │  (anonymous)
                            │  ┌──┐ ┌──┐ ┌──┐ │
                            │  │A │ │B │ │G │ │
                            │  └──┘ └──┘ └──┘ │
                            └────────┬────────┘
                                     │ tap tile
                                     ▼
                            ┌─────────────────┐
                            │  PIN entry      │  (skip if pin: null)
                            │   _ _ _ _       │
                            │  [1][2][3]      │
                            │  [4][5][6]      │
                            │  [7][8][9]      │
                            │     [0]         │
                            └────────┬────────┘
                                     │ correct PIN
                                     ▼
              ┌──────────────────────────────────────────────┐
              │  Home (kid's tile launcher)                  │
              │                                              │
              │   🔍 [search anything...]                    │
              │                                              │
              │   ┌────┐ ┌────┐ ┌────┐ ┌────┐                │
              │   │📚  │ │🎓  │ │🎬  │ │🎨  │                │
              │   │Wiki│ │Khan│ │Vids│ │Play│                │
              │   └────┘ └────┘ └────┘ └────┘                │
              │   ┌────┐ ┌────┐ ┌────┐ ┌────┐                │
              │   │📖  │ │🗺  │ │💬  │ │🤖  │                │
              │   │Book│ │Maps│ │Chat│ │Ask │                │
              │   └────┘ └────┘ └────┘ └────┘                │
              │                                              │
              │  [⤴ switch user]                             │
              └──────┬───────────────┬──────────┬────────────┘
                     │               │          │
                     ▼               ▼          ▼
              ┌──────────────┐ ┌──────────┐ ┌──────────┐
              │ Search       │ │ /launch/X│ │ AI ask   │
              │ results      │ │ → backend│ │          │
              │ Read|Watch|… │ │          │ │          │
              └──────────────┘ └──────────┘ └──────────┘
```

### Avatar grid (`/`)

- Renders all `kids` from `kids.yml` plus the `guest` tile.
- Each tile: avatar image (large), name beneath, optional PIN-lock
  indicator.
- TTS: tapping a tile speaks the name aloud (helps pre-readers
  find their tile).
- Adults / contacts not shown here — they appear only in messaging
  contact lists.

### PIN entry (`/login`)

- Numeric keypad, no shift / no special characters.
- Visual feedback: filled circles for entered digits.
- Wrong PIN: shake animation, no error text (don't hint at how
  many digits are wrong).
- After 5 wrong attempts: 60-second cooldown. (Family-context
  threat model is mild but a kid trying their sibling's PIN
  should bounce off.)
- Skipped entirely if the kid's `pin: null` in `kids.yml`.

### Home (`/home`)

- Search bar prominent at top.
- 4×N tile grid below, populated based on the kid's permissions.
- Greyed-out tiles for services in curfew (with tooltip explaining).
- Hidden tiles entirely for services the kid lacks permission for.
- "Switch user" button at the bottom corner.
- Per-kid colour theme (read from `kids.yml.theme`, optional).

### Search results (`/search?q=…`)

- Powered by the federated search aggregator (see `search.md`).
- Grouped by kind: **Read about it / Watch it / Learn it / Play it / Read a book**.
- Each result is a card with thumbnail, title, snippet, source label.
- Tapping a result opens the source service via the broker
  (deep-link to the article, video, or course).
- "Ask the helper" button opens the AI gateway with the query
  pre-filled.

### Switch user

- Logs out of all services (best-effort, see `identity.md`).
- Returns to avatar grid.
- Visible feedback: brief "Goodbye, Alice" before transition.

### Settings (`/settings`, admin-only)

- Not shown to kids.
- Reachable only via `admin.kids` (HTTP Basic).
- Renders the latest content-update report inline.
- Per-adapter health badges.
- Lists each kid's recent activity (top services, top searches).
- Inline editor for `kids.yml` and `manifest.yml`, with a "save"
  that performs a git commit and triggers provisioner / updater.

### Error states

- **No backend service available**: friendly fallback screen with
  the launcher logo. Kids should never see a stack trace.
- **Adapter health check failed**: tile is greyed out with an "I'm
  sleeping" indicator. The broker refuses the launch, but politely.
- **PIN forgotten**: no in-product reset. The admin (Mick) updates
  `kids.yml` and re-provisions. (Family threat model: a kid having
  to ask a parent is fine.)

## Data model

### Frontend (in-memory)

```typescript
// types.ts
type KidId = string;
type ServiceName =
  | "kiwix" | "kolibri" | "sugarizer" | "calibre"
  | "peertube" | "synapse" | "tileserver" | "ai";

interface Kid {
  id: KidId;
  name: string;
  age: number;
  ageBand: string;
  avatar: string;
  pinRequired: boolean;
  permissions: Permissions;
  themeColour?: string;
}

interface Permissions {
  services: Record<ServiceName, ServicePermission>;
  messaging: { enabled: boolean; contacts: KidId[] };
  aiCompanion: boolean;
}

interface ServicePermission {
  enabled: boolean;
  curfewAfter?: string;          // "19:00"
  dailyLimitMinutes?: number;
}

interface Session {
  kid: Kid;
  startedAt: number;
  lastActivityAt: number;
  expiresAt: number;
}

interface SearchResult {
  id: string;
  title: string;
  snippet: string;
  kind: "article" | "video" | "course" | "exercise" | "book" | "activity";
  source: string;
  thumbnail?: string;
  url: string;
  durationSec?: number;
  ageBand?: string;
  score: number;
}
```

### Backend (broker — FastAPI)

```python
# broker/state.py
@dataclass
class KidsConfig:
    kids: list[Kid]
    contacts: list[Contact]

    @classmethod
    def load(cls) -> "KidsConfig":
        # Read /srv/treehouse/config/kids.yml, validate against schema,
        # return parsed structure.
        ...

@dataclass
class Session:
    kid_id: str
    started_at: datetime
    last_activity_at: datetime
    signed_token: str            # set as cookie

# state/launcher/credentials.sqlite — see identity.md
```

The broker holds no in-memory session state across restarts —
sessions live in signed cookies (HMAC, key from `secrets.yml`).
Restart-safe by construction.

## The broker pattern, in code

### Endpoint contract

```
POST /api/login           {kid_id, pin?} → 302 to /home + Set-Cookie kidsession
POST /api/logout          → 302 to / + clear cookies, walk each adapter's logout
GET  /api/whoami          → {kid_id, session_age_seconds, permissions}
GET  /api/launch/<svc>    → 302 to <svc>.kids + Set-Cookie for that service
GET  /api/search?q=...    → search results (proxies to searchd)
POST /api/ask             → {response, sources} (proxies to aigateway)
GET  /api/admin/health    → {service: status, ...}    (admin only)
```

### Launch flow, expanded

```python
# broker/launch.py

@router.get("/api/launch/{service}")
async def launch(service: str, request: Request) -> RedirectResponse:
    session = require_session(request)
    kid = kids_config.find(session.kid_id)

    # 1. Permission check
    perm = kid.permissions.services.get(service)
    if not perm or not perm.enabled:
        return refusal_page(reason="permission")

    # 2. Curfew check
    if perm.curfew_after and now().time() >= perm.curfew_after:
        return refusal_page(reason="curfew", until=tomorrow_morning())

    # 3. Daily limit check (read from sessions log)
    if perm.daily_limit_minutes and minutes_used_today(kid, service) >= perm.daily_limit_minutes:
        return refusal_page(reason="limit")

    # 4. Mint backend session
    adapter = adapters[service]
    token = await adapter.auth_token(kid.id)

    # 5. Build redirect with backend cookie injection
    response = RedirectResponse(url=f"https://{service_host(service)}", status_code=302)
    for cookie in token.cookies_to_set(domain=service_host(service)):
        response.set_cookie(**cookie)

    # 6. Log for observability
    activity_log.record(kid.id, service, "launch")

    return response
```

The launch endpoint is the heart of the broker. Its tests are the
heart of the test suite.

### Logout cascade

```python
@router.post("/api/logout")
async def logout(request: Request) -> RedirectResponse:
    session = optional_session(request)
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie("kidsession", domain=".kids")

    if session:
        # Best-effort: walk each adapter, ignore failures
        for adapter in adapters.values():
            try:
                await adapter.logout(session.kid_id)
            except Exception as e:
                log.warning(f"logout failed for {adapter.name}: {e}")

    return response
```

## LauncherClient — the testable layer

The frontend's logic lives in a single class:

```typescript
// launcher/src/lib/LauncherClient.ts
export class LauncherClient {
  constructor(private readonly fetch: typeof window.fetch) {}

  async login(kidId: string, pin?: string): Promise<Session> { ... }
  async logout(): Promise<void> { ... }
  async whoami(): Promise<Kid | null> { ... }
  async launch(service: ServiceName): Promise<void> { ... }
  async search(q: string): Promise<SearchResult[]> { ... }
  async ask(question: string): Promise<AiResponse> { ... }
}
```

Components import this class and call its methods. They never
construct `fetch` calls directly. This means:

- Unit tests construct a `LauncherClient` with a stubbed fetch and
  exercise the contract entirely without a browser.
- Component tests render Svelte components with an injected mock
  `LauncherClient`.
- E2E tests use the real client against a real backend.

Without this discipline, every test ends up either fully integrated
(slow, flaky) or fully mocked (cheap, useless). The class is the
seam.

## Test strategy

### Unit (Vitest)

- `LauncherClient` methods, with `fetch` stubbed.
- Pure functions: PIN validation, time-of-day curfew check, search
  result grouping, age-band filter logic.
- Fast: <1s per file, hundreds of tests in seconds.
- Run on every save in dev mode.

### Component (Vitest + @testing-library/svelte)

- Render each component in isolation with a mocked `LauncherClient`.
- Assertions: "PIN entry shake animation triggers on wrong PIN",
  "Tile is greyed out when service unhealthy", "Search results
  group correctly by kind".
- ~10s for full suite.

### Backend integration (pytest)

- Spin up the FastAPI broker against a fixtures-only adapter set
  (no real Kolibri etc).
- Test endpoint contracts: login → cookie set, launch → correct
  redirect, logout → cookie cleared.
- Per-test database, isolated.

### End-to-end (Playwright)

- `compose.test.yml` brings up a smaller real stack with seeded
  test fixtures (one ZIM, one Kolibri channel, two test kids).
- Run flagship flows:
  - Avatar → PIN → Home
  - Home → Khan Academy tile → Kolibri dashboard
  - Home → Search "volcanoes" → results from kiwix and kolibri
  - Home → Switch user → re-login as Bob
- Screenshots saved with each run; PR diff catches visual regressions.
- ~5 minutes for full suite. Runs on every PR.

### Visual regression

- Playwright's `toHaveScreenshot` for every screen at standard
  device viewports (iPad, 7" Android tablet, family TV).
- Baseline images in `tests/e2e/screenshots/` (committed, with
  intentional updates flagged in PR descriptions).
- A change to a tile's CSS without an intentional screenshot
  update fails the test — keeps visual drift out of the codebase.

## Test fixtures

`tests/fixtures/` contains:

- `manifest.test.yml` — minimal manifest: one ZIM, one Kolibri
  channel, no YouTube (network-fragile), no OSM.
- `kids.test.yml` — two test kids:
  - `alice-test` (age 8, with PIN `1234`, full permissions)
  - `bob-test` (age 5, no PIN, restricted permissions)
- `seed-zims/wikipedia_for_schools.zim` — small (5 GB) ZIM
  committed via Git LFS or downloaded by the test bootstrap.
- `seed-kolibri/` — pre-imported Kolibri channel data.

`compose.test.yml` mounts these fixtures and overrides the
manifest paths so the test stack is deterministic.

## State on the device

The launcher writes minimal state to the kid's browser:

- `kidsession` cookie (HMAC-signed, scoped `.kids`, 30-min expiry,
  rolling).
- Per-service session cookies / localStorage entries set by the
  broker during `/api/launch/<svc>`.
- Optional: a `lastUsedKid` cookie to skip the avatar grid on
  dedicated-device deployments. Not used by default; opt-in
  via `kids.yml.kids[].dedicated_device: true`.

No client-side storage of credentials, PINs, or anything sensitive.
The PIN never leaves the device's render pipeline as anything but
an HTTP body to `/api/login`, which is a localhost-segment request.

## Accessibility

- TTS labels on every tile and button. Web Speech API where
  available; pre-rendered audio fallbacks where not.
- Sufficient colour contrast (WCAG AA at minimum).
- Keyboard navigation for the avatar grid and PIN entry (helps
  external-keyboard users and testing).
- Reduced-motion respected (`prefers-reduced-motion`): the
  shake animation, tile transitions, etc. honour the OS setting.

## Open questions

- **Should the launcher live on every device as an installable
  PWA?** Eliminates the URL bar (a frequent source of "wait, what
  is google.com" curiosity from kids). Phase 5+ feature.
- **Should there be a "guided tour" mode for first-time use?** A
  one-time walkthrough showing where things are. Tempting but
  probably premature; kids find their way around small UIs quickly.
- **Should the home tiles auto-recommend?** "Alice, you were
  watching this video — pick up where you left off." Nice to have;
  needs activity tracking, which exists for the search aggregator.
  Phase 5+.
- **Should the search bar accept voice input for pre-readers?**
  Web Speech API supports it client-side. Worth a try once core
  flows are stable.
