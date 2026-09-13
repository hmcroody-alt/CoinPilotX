# PulseSoc — Web Target Architecture

**Scope:** Stages 8–11 — web client architecture, the backend workstream, the database
workstream, and the URL / native-app relationship.

**The governing constraint, stated once:** the native app is the product source of truth, and
there is **one platform**. The web client is a new *consumer* of a proven API, not a new API,
not a second database, and not a second copy of the domain logic.

Everything below is derived from the inventories. Where a decision is genuinely open, it is
listed in §12 as an owed decision rather than papered over.

---

## 0. The architecture in one page

```
            ┌──────────────────┐        ┌──────────────────┐
            │  iOS native app  │        │   Web client     │
            │  Expo 54 / RN    │        │   (new)          │
            └────────┬─────────┘        └────────┬─────────┘
                     │  Bearer + refresh         │  Cookie + refresh
                     │  expo-secure-store        │  HttpOnly cookies
                     └────────────┬──────────────┘
                                  │
                     ┌────────────▼──────────────┐
                     │   Shared HTTP API         │   1,331 /api rules
                     │   /api/*  (Flask)         │   45 shared today
                     └────────────┬──────────────┘
                     ┌────────────▼──────────────┐
                     │   Shared domain logic     │   services/ — ~707 files
                     │   services/               │   ~272k lines
                     └────────────┬──────────────┘
                     ┌────────────▼──────────────┐
                     │   Shared data plane       │   PostgreSQL 18.6
                     │   884 tables              │   ONE database
                     └────────────┬──────────────┘
                     ┌────────────▼──────────────┐
                     │   Shared infrastructure   │   R2 · Mux · Stripe ·
                     │                           │   Brevo · FCM/APNs/VAPID
                     └───────────────────────────┘
```

**Four architectural guarantees this document commits to.** Each is a rule a reviewer can
reject a pull request against.

| # | Guarantee | Enforced by |
|---:|---|---|
| **G1** | The web client never opens a database connection. It consumes the same JSON the phone consumes. | No `DATABASE_URL` in any web build; no SQL in the client workstream |
| **G2** | Domain logic is added to `services/`, never to a client. If the web needs a rule the phone lacks, the rule goes in the service and both clients get it. | Code review; a rule implemented twice is a defect |
| **G3** | The web ships on the **apex origin** `pulsesoc.com`. Not a subdomain. | `SESSION_COOKIE_DOMAIN` is unset (§3.5) and the AASA claims only the apex (§7) |
| **G4** | Shape resolution (media ids, polymorphic entity ids, 106-column projections) happens at the API layer. The client receives resolved objects. | `PULSESOC_DATABASE_GAP_ANALYSIS.md` §7 |

**G1 is the one that pays for the whole project.** It is why the answer to "how much backend
work is this" is *a small, well-defined list* (§8) rather than *a rewrite*.

---

## 1. Why the obvious architecture is wrong here

The default 2026 answer — *Next.js on Vercel, talk to the Flask API over CORS* — is blocked by
three independently verified facts. It is worth writing down why, because every new engineer
will propose it.

| Fact | Where verified | Consequence |
|---|---|---|
| `SESSION_COOKIE_DOMAIN` is **not set**; cookies are host-only on the apex, and `redirect_www_to_apex_domain` reinforces it | `bot.py:1213–1222`, `bot.py:2565` | A client on `app.pulsesoc.com` is **unauthenticated**. There is no shared session |
| The iOS Associated Domains entitlement claims only `pulsesoc.com`; the served AASA claims only `/pulse/*` and `/search*` | `services/native_app_links.py:31–34` | A new origin or a new path family is **silently ignored by every installed copy of the app** until a new iOS build ships |
| CSRF exemption for `application/json` is justified in-code by *"this app sets no CORS headers"* | `bot.py:3451–3456` | **Adding CORS silently removes CSRF protection from every JSON route in the app** |

That third row is the sharpest. `grep` for `Access-Control-Allow-Origin`, `flask_cors` and
`CORS(` across the backend returns **zero matches** — the exemption's premise is true today.
It is a load-bearing dependency between two facts that live 3,000 lines apart, and a
cross-origin web client is precisely the change that breaks it.

> **Rule:** any pull request that adds a CORS header to the Flask app must, in the same
> diff, add JSON CSRF enforcement. These two changes are not separable.

**Therefore: same-origin, or nothing.** Everything in §2 follows from that.

---

## 2. Client architecture

### 2.1 The deploy-image constraint

`nixpacks.toml` installs `python311` and `ffmpeg`. **There is no Node in the production
image.** The web client therefore cannot be built at deploy time and cannot be server-rendered
by a Node process.

That is not a problem — it is a useful forcing function. It rules out SSR-at-runtime and leaves
one clean shape: **build in CI, ship static artifacts, serve them from Flask.**

### 2.2 The decision

**A two-surface web application on one origin:**

| Surface | Paths | Rendering | Why |
|---|---|---|---|
| **Marketing / SEO / legal** | `/`, `/legal/*`, `/education/*`, the 139 static pages | **Server-rendered Jinja**, `autoescape=True` | Must be crawlable, must render without JS, must carry the App Store CTA. Already works |
| **The product** | `/pulse/*` (+ `/dashboard/*`, `/settings/*`) | **SPA**, built in CI, served as hashed static assets | Authenticated, behind a login wall, no SEO value, needs real client state |

Flask serves the SPA the standard way: a catch-all route under the product prefix returns the
built `index.html`; hashed assets come from `static/`. The API is untouched, same-origin, and
cookie auth works with no CORS and no CSRF regression.

**Framework:** React 19 + TypeScript, matching the native app. This is not a stylistic choice —
it is what makes the design-token translation (`PULSESOC_WEB_DESIGN_SYSTEM_MAP.md` §9) and the
API-module port (`mobile-native/src/api/*`) a translation rather than a re-derivation.

**Explicitly not chosen:** React Native Web. The mission requires desktop to be a premium
multi-column experience, not a stretched phone, and RNW makes the native layout the path of
least resistance. The shared layer is the *API contract and the design tokens*, not the view
code.

### 2.3 What gets deleted, and what is carefully kept

From `PULSESOC_REUSE_VS_REBUILD_MATRIX.md`, the two items that shape the client:

- **The inline HTML layer does not come forward.** 46.7% of `bot.py` is HTML/CSS/JS string
  literal across 5 competing shell functions, and `clean_html()` — the de-facto sanitiser at
  2,217 call sites — is a tag stripper that passes `<img src=x onerror=alert(1)` through
  intact. The rebuild resolves this class **structurally**: Jinja `autoescape=True` on the
  marketing surface, JSX on the product surface. Both make the default safe. A find-and-replace
  across 2,217 sites is explicitly *not* the plan.
- **`services/app_links.py` and `templates/_app_link_cta.html` come forward unchanged.** They
  are the best-engineered part of the existing web layer and they encode correctness
  guarantees (`app_store_url()` refuses a non-`apps.apple.com` env override; `build_app_link()`
  raises rather than emitting a link the shipped binary cannot resolve). The SPA consumes the
  same canonical source — see §7.4.

### 2.4 How the artifacts reach the box — and the staleness that buys

§2.1 says "build in CI, ship static artifacts." It does not say how they arrive, and the
answer is forced: **Railway deploys from git, and the image has no Node.** So the build
output is *committed*. `web/` holds the source, `static/app/` holds the built bundle, and
both are in the repository.

That is the only option available, but it is worth being explicit that it is a trade rather
than a free choice. It converts a build problem into a **staleness** problem, and staleness
is the worse-behaved of the two because it is silent:

> Edit `web/src/`, forget to rebuild, commit. The Python tests pass. The app boots. `/health`
> answers 200. The page renders. It renders last week's bundle.

Nothing in any existing suite compares the two halves — a unit test runs the *source*, a
browser runs the *artifact*. This is the same shape as the TestFlight build 5 failure: two
sides disagreeing while each looks healthy alone.

**The gate.** `web/scripts/fingerprint.mjs` records a sha256 per source input at build time
into `static/app/build-source-hash.json`; `scripts/ops/web_build_freshness_gate.py`
recomputes it and fails on any difference. `.github/workflows/web-build.yml` runs the
protection tests, then `npm ci`, then the build, then the gate.

Two details of that design are deliberate and should survive edits:

- **It compares source inputs, not output bytes.** Diffing CI's bundle against the committed
  one would make the gate depend on the bundler being byte-reproducible across machines and
  Node versions. It mostly is — and "mostly" is precisely what produces a false alarm on a
  Node minor bump, which is how a check gets switched off. Hashing inputs is exactly as
  strong for the failure being prevented and is deterministic by construction. It is also
  what lets a developer on a different Node version rebuild without tripping the gate.
- **`package-lock.json` is one of the inputs.** A dependency bump changes the bundle with no
  diff anywhere under `src/`, and that is the one stale case a source-only fingerprint would
  miss.

**Sourcemaps are off.** Committed artifacts mean every build adds its maps to git history
permanently (~1 MB for even an empty scaffold), and they publish full source for a product
behind a login wall. Nothing consumes them today — there is no error reporter to symbolicate
against. Turn them back on together with one, and upload the map to it rather than
committing it here.

---

## 3. Authentication architecture

**Auth is the only genuinely blocking backend gap.** Everything else in the API surface is
either ready or merely unexercised.

### 3.1 The session store already exists

The most common assumption — *"a web client needs a session store"* — is false.
`mobile_security_sessions` is, despite its name, already the shared store: **7,446 `web`
sessions** against 2,686 `ios`, with refresh rotation, `session_family_id`, `reuse_detected_at`
and `revoked_reason` all working for browsers today. **No new session infrastructure.**

### 3.2 The credential model

| | Web | Native |
|---|---|---|
| Primary | Flask session cookie | `Authorization: Bearer` |
| Long-lived | `pulse_refresh_session` cookie (rotating) | refresh token in expo-secure-store |
| Login | `POST /login` (form) | `POST /api/mobile/auth/login` |
| CSRF applicable | **yes** — ambient authority | no — custom header |

Both legs exist. `/login` already mints a session cookie *and* bearer/refresh tokens in one
handler. The work is not building auth; it is **fixing four specific things**.

### 3.3 Fix 1 — `account_user_id()` must verify both credentials, not short-circuit

`bot.py:3658–3659` is a left-to-right `or` chain. When a request carries both a valid session
cookie and a valid bearer token, Python short-circuits on the cookie and
`g.mobile_access_user_id` — set *only* inside the bearer branch — is never set. Write gates
that read it as their "CSRF-safe native caller" signal then refuse. Reads pass, writes 403.

`services/business_os_commerce_routes.py:46–73` already contains the correct fix, in a comment
that describes the bug in its own words: **re-verify the bearer independently, require it to
name the same user as the cookie, deny on mismatch.** That model must be lifted into
`account_user_id()` itself rather than re-implemented per route pack — today it protects only
the 37 commerce-gateway routes, while `bot.py:18415` carries a comment asserting the opposite
is true for the ads family, which is an unpatched instance of the same bug.

This is a prerequisite for the web client, not a cleanup: a browser and a phone signed into the
same account is the normal steady state, and "which identity wins" must be decided
deliberately.

### 3.4 Fix 2 — a default-deny auth decorator

`grep -oE '^@[A-Za-z_.]*' bot.py` returns **seven** decorator kinds and **not one of them is an
auth decorator**. All 1,775 in-file routes gate themselves by calling one of at least **17
different helpers** inside the view body — `api_account_user()` 574 times, `require_account()`
152 times, and so on.

**There is no static way to prove a route is protected, and adding a route with no auth at all
is a silent, test-passing change.** A second client is exactly the moment that bites.

> **The single highest-leverage backend change in this project** is a decorator with a
> registration-time check: at boot, assert that every registered rule is either decorated or
> on an explicit public allowlist, and fail the boot otherwise. It converts an unprovable
> property into a startup assertion.

Migration is mechanical and can be incremental: decorate, keep the in-body call, remove the
in-body call once the decorator is proven on that family.

### 3.5 Fix 3 — a browser device id

`pulse_security_core.device_fingerprint` keys on `X-PulseSoc-Device-Id` / `X-Device-Id`. **A
browser sends neither**, so the fingerprint collapses to the User-Agent and every Chrome-on-
Windows visitor shares one rate-limit bucket and one device identity.

The web client must mint a stable random per-browser id, persist it in `localStorage`, and send
it on every request. This is a client change plus a documented contract — no backend change —
and it makes the user and device legs of the existing limiter work for browsers. It also gives
the session table a meaningful `device_label` for web, which the "sign out everywhere" UI needs
in order to say anything useful.

### 3.6 Fix 4 — split the signing keys

`COINPILOTX_SECRET_KEY` signs Flask session cookies **and** mobile bearer tokens
(`bot.py:3619`), with no key id and no rotation scheme. Rotating it to invalidate compromised
web sessions logs out every phone in the field simultaneously. Adding a third client widens the
blast radius again. **Split before launch, not after an incident.**

### 3.7 One CSRF contract

Two contracts coexist today: the global `verify_csrf()` reads **only `request.form`**, while
`pulse_ads_verify_write()`, `_csrf_ok()` and `_business_os_ent_csrf_ok()` accept an
`X-CSRF-Token` header. The blanket `before_request` hook covers only `/admin` and `/api/admin`
— **all 406 `/api/pulse` routes have no blanket CSRF enforcement at all.**

Target: one header-based contract (`X-CSRF-Token`), one helper, enforced by the same decorator
as §3.4 for cookie-authenticated mutating requests. `SESSION_COOKIE_SAMESITE="Lax"` stays, but
it is a property of the visitor's browser, not an application control, and must not be the only
line.

### 3.8 One thing this architecture does *not* fix

The Flask cookie session has **no server-side row** — there is no `SESSION_TYPE` and no
Flask-Session backend, so it is the default client-side signed cookie. **A "sign out
everywhere" button cannot kill a web cookie session today.** Combined with
`PERSISTENT_SESSION_DAYS = max(3650, env)` — a 10-year floor that the env var can only
*lengthen* — a browser session, once established, is effectively permanent and irrevocable.

That is a security decision with a real cost, and it belongs in
`PULSESOC_WEB_SECURITY_MODEL.md` with an explicit accept-or-fix. It is named here so the
architecture does not appear to have solved it.

---

## 4. Data access architecture

**G1 restated: the web client never touches the database.** Five findings from the database
inventory make this a correctness requirement rather than a style preference:

| Finding | What a direct-query web client would get wrong |
|---|---|
| `pulse_posts.user_id = 0` on **81% of rows** (1,915 posts), a sentinel with no matching user | An `INNER JOIN users` **silently drops 81% of the feed.** No error, no warning |
| `media_ids_json` is TEXT; resolution happens in Python at `services/pulse_feed_engine.py:589` | There is no join to write. The client would have to replicate Python |
| `entity_id` in notifications is TEXT and polymorphic | Deep links resolve per-type. Wrong resolution = wrong destination |
| `users` is a **106-column** god table | Over-fetch on every profile view |
| `marketplace_*` uses **integer** ids, `business_os_mkt_*` uses **text** | Postgres raises where SQLite silently coerced — **on the money path** |

Every row resolves the same way: **the API returns resolved objects; the client never sees the
raw shape.** That is the architectural guarantee, and it is also why the 236 native-only
endpoints are an asset — they already do this resolution correctly for the phone.

---

## 5. Realtime architecture — polling, by decision

This is a named architectural decision, not a gap to close mid-rebuild.

`bot.py:89367` states the reason in the code: *"Long-lived browser streams can exhaust the main
Gunicorn worker pool."* Both SSE routes return `204` with
`X-Pulse-Realtime-Transport: polling`. **There is no WebSocket server**, and the web layer has
zero `new WebSocket` and zero `RTCPeerConnection` today.

The deployment topology forbids the alternative: `gunicorn ... --workers 4 --threads 8`. A
long-lived stream occupies a thread for its lifetime; 32 concurrent streams is the entire
capacity of the service.

| Capability | Web target |
|---|---|
| Message delivery | Poll. Visible-tab interval, backoff when hidden |
| Typing / presence / read receipts | Poll, reduced fidelity, explicitly |
| Push notifications | **Web Push (VAPID) already exists and works** — use it for the out-of-tab case |
| Voice / video / livestream | ⛔ **Protected system. Inventory only. Out of scope.** RTC is Agora |

**Changing this means changing the deployment topology** — a separate ASGI/WebSocket service,
its own scaling, its own auth bridge. That is a project, and it is not this project. The
Messages phase is scoped against polling.

> **Design consequence the UI must absorb:** a two-pane desktop messenger with a visible
> latency floor needs optimistic local echo and an explicit send-state, not a spinner. Web
> Push covers the "user is not looking at the tab" case that polling handles worst.

---

## 6. Media and uploads

The native app uses presigned R2 multipart upload: init → presigned part URLs → complete.
`services/media_upload_sessions.py:142` calls `create_multipart_upload`, `:176` issues
presigned `upload_part` URLs, and **`:211–214` requires the client to send each part's
`ETag` back** in the complete call.

> **In a browser, `fetch` cannot read the `ETag` response header of a cross-origin upload
> unless the R2 bucket's CORS policy lists `ETag` in `ExposeHeaders`.** Without it every
> multipart upload gets `null` for every part and the complete call fails validation at
> `:212`.

**No CORS configuration exists anywhere in the repository** — it is set in the Cloudflare
dashboard, so this must be *verified against the live bucket*, not assumed either way. It is
the first item in the Phase 0 checklist because it silently blocks avatar upload, cover upload,
reels, post media, and the seller-application document flow simultaneously.

Required R2 CORS: `AllowedOrigins: ["https://pulsesoc.com"]`, `AllowedMethods: [GET, PUT, HEAD]`,
`AllowedHeaders: ["*"]`, **`ExposeHeaders: ["ETag"]`**.

Second constraint: Flask caps POST bodies well below the direct-to-R2 path, so **the browser
must upload direct to R2**, never proxy through the app. That is the same path the phone uses.

---

## 7. URL architecture and the native-app relationship

### 7.1 The constraint that decides everything

`services/native_app_links.py:31–34` builds the served AASA with exactly two components:

```python
components = [
    {"/": "/pulse/*",  "comment": "PulseSoc native objects and workflows"},
    {"/": "/search*",  "comment": "PulseSoc search"},
]
```

**A brand-new path family cannot reach the installed app.** iOS caches the AASA at install
time; a new prefix requires a new AASA *and* re-association, and in practice a new build. Any
URL scheme that assumes otherwise is unshippable for the current binary.

**This resolves the open `/open/...` question directly: a new `/open/...` family must not be
introduced.** Deep links continue to live under `/pulse/*`. `/open/...` would be ignored by
every copy of the app in the field — silently, with no error, which is the worst failure shape
available.

### 7.2 A second, sharper failure mode

The AASA route returns **503** when `PULSESOC_APPLE_TEAM_ID` is unset or malformed
(`bot.py:123670`, `services/native_app_links.py:20–22`). A 503 AASA means universal links are
**entirely dead** — every `pulsesoc.com/pulse/...` link opens Safari instead of the app, with
no in-app symptom and no crash.

**Action:** assert `GET /.well-known/apple-app-site-association` returns `200` with a non-empty
`details[]` as a production health check, not a one-time manual verification. This is cheap and
it protects the single mechanism the app-promotion requirement depends on.

### 7.3 The tension worth naming

`/pulse/*` is simultaneously (a) where the web product lives and (b) what the app claims. On an
iOS device with the app installed, following a `/pulse/...` link from another app opens the
app, not the website.

This is mostly **desirable** — it is exactly the app-promotion behaviour the product wants —
and it is already handled:

- `is_web_intent_path()` (`services/app_links.py`) protects privacy, terms, support, login,
  checkout and `/dashboard` from being converted into app launches. Those paths must stay
  reachable in a browser or the money and legal paths break.
- `?pulse_app=1` marks app-intent explicitly.
- In-SPA navigation is `history.pushState` and does **not** trigger universal links, so the web
  product is fully usable once loaded.
- Apple's own "open in browser" banner is the escape hatch for a user who wants the web.

**Rule for the rebuild:** every new path added under `/pulse/*` must be classified as
app-intent or web-intent at the moment it is created, via `is_web_intent_path()`. An
unclassified path defaults to app-intent and will disappear into the app on iOS.

### 7.4 App promotion (standing requirement)

The rebuilt site must promote the iOS app throughout.

- **Canonical source only.** `services/app_links.py` → `app_store_url()` /
  `build_app_link()`, surfaced on the marketing side through the
  `templates/_app_link_cta.html` macros (imported `with context`, or they raise
  `UndefinedError` at render time). **Never hand-write the App Store URL.** The helper
  deliberately ignores a `PULSESOC_APP_STORE_URL` env override that is not an
  `apps.apple.com` URL, so a bad Railway variable cannot become an open redirect.
- **For the SPA**, expose the same values through a small bootstrap endpoint rather than
  duplicating the constant into the bundle. One source, two renderers.
- **Styling:** the design system map §11 measured white-on-`#32e6b3` at ~1.8:1 — it **fails
  WCAG AA**. The CTA must use the `--pulse-on-accent` token, not white.

### 7.5 URL shape

| Surface | Shape | Notes |
|---|---|---|
| Product | `/pulse/<area>/<id>` | Matches AASA. Stable, shareable, app-openable |
| Profiles | `/@username` | Requires the `lower(users.username)` unique index — **a sequential scan today** |
| Marketing | existing paths | Unchanged; SEO value is real |
| Admin | `/admin/*` | Separate workstream (§8.4) |
| Legacy | `/arena/*` | Deletion candidate pending the dynamic-call audit |

---

## 8. The backend workstream (Stage 9)

Deliberately small. **236 endpoints need callers, not construction.**

### 8.1 Required before the first authenticated page

| # | Change | Why |
|---:|---|---|
| 1 | Fix `account_user_id()` to verify both credentials (§3.3) | Two clients on one account is the steady state |
| 2 | Auth decorator + boot-time default-deny assertion (§3.4) | Converts an unprovable property into a startup check |
| 3 | Unify the CSRF contract on a header (§3.7) | Two contracts, and `/api/pulse` has neither |
| 4 | Split session-cookie and bearer signing keys (§3.6) | One rotation currently logs out every phone |
| 5 | Verify + set R2 CORS `ExposeHeaders: ["ETag"]` (§6) | Blocks every browser upload path at once |
| 6 | Accept `X-PulseSoc-Device-Id` from browsers (§3.5) | Contract only; makes the existing limiter work |

### 8.2 Required before launch

| # | Change |
|---:|---|
| 7 | Shared rate-limit store (Redis) + turn on distributed mode. Per-worker dicts mean the real limit is ~4× the configured one |
| 8 | `GET /health/routes` as a hard deploy gate, extended with a feature-flag snapshot — ~382 routes live inside `except Exception` registrations and **pack #1 alone is 162 routes owning all messaging and calling** |
| 9 | Session retention — 9,730 dead `mobile_security_sessions` rows still carry `user_agent` + `ip_hash`. **Tombstone, do not delete:** the row is what makes refresh-token reuse detectable (gap analysis §5a) |
| 10 | Fix `bot.py:28760` — raw database cells rendered into an admin session with no sanitisation. **Independent of the rebuild schedule** |

### 8.3 Explicitly *not* in scope

- New endpoints for parity. The 236 exist.
- A backend rewrite. `services/` is untouched by a client rebuild.
- CORS. See §1 — and if it is ever added, §3.7 ships in the same diff.
- Anything under `/api/pulse/live` or `/api/calls`. **Protected systems. Inventory only.**

### 8.4 Named as its own workstream, not a phase tail

The admin console is **296 page routes + 90 API routes — larger than the consumer product it
administers** (173 `/pulse` routes). It has the strongest authorization in the codebase
(330/330 gated, audited, fails closed) *and* the worst XSS exposure. Any plan that treats it as
final cleanup has mis-scoped roughly a third of the work.

One specific exclusion: `require_admin_password()` (`bot.py:14939`) accepts a shared password
from the **query string**, compares with `==` rather than `compare_digest`, and produces no
attributable audit trail across its 9 call sites. **Those routes must not be exposed to the web
rebuild.**

---

## 9. The database workstream (Stage 10)

**Zero new tables. Zero new databases. Zero destructive migrations.** There is no migration
framework — schema is imperative in `bot.init_db()` and every change must be idempotent.

| Type | Count | When |
|---|---:|---|
| **Decisions** (duplicate lineages) | 7 | Before the corresponding phase — free now, expensive later |
| Index additions (`CONCURRENTLY`) | 3 | Phase 0 |
| Index removals (duplicates) | 2 | Phase 0 |
| Data decision (`user_id = 0`) | 1 | Before the feed phase |
| FK additions on the verified-clean core social graph | 11 | `NOT VALID` → `VALIDATE` |
| Maintenance job (session TTL) | 1 | Before launch |

The three index additions are the ones a web client feels immediately: `lower(users.username)`
(**`/@username` is a sequential scan today**), `lower(users.email)` (login is a seq scan), and
`active_sessions.session_hash` (only a pkey index exists). All three need a partial predicate
to tolerate the handful of blank values.

**Method constraints, non-negotiable:** never run DDL against production to test it — verify on
a throwaway Docker Postgres 18 first, because SQLite reproduces neither the type behaviour nor
the constraint behaviour. `CONCURRENTLY` for every index on a live table. And do not add
`ensure_schema(conn)` call sites — passing a route's connection skips the commit, the DDL rolls
back, and a second connection then blocks on the uncommitted catalog lock. There are ~26
unswept call sites already.

Full detail: `PULSESOC_DATABASE_GAP_ANALYSIS.md`.

---

## 10. Responsive architecture

The mission requires desktop to be a premium experience, not a stretched phone. The design
system map §10 sets the breakpoints (phone <900 / desktop 900–1479 / wide ≥1480) and one
governing rule:

> **The feed column never exceeds 884px at any breakpoint. Extra width buys additional
> columns, never wider rows.**

Both the breakpoints and the measure were corrected in Phase 1c. This section used to read
`768 / 1119 / 1599 / 1600` with a 680px feed, and tracing
`mobile-native/src/screens/HomeScreen.tsx` found none of those numbers in the app: it caps
content at 1480 with 12px padding, fixes its rails at 226 and 314 with a 16px gap, and gives the
feed `flex: 1`. 884 is the residual. 900 is the app's own `wideCanvas` threshold. Keeping the
originals would have made the website a second opinion about the product's proportions, which is
precisely what "the native app is the source of truth" rules out.

Where desktop should deliberately *exceed* native: the ads campaign wizard (native's is a
1,985-line multi-step drilldown; desktop wants side-by-side targeting + estimate + persistent
preview — treat as **web-first**, not a port), Business OS dashboards, marketplace browsing
with a persistent facet panel, a two-pane messenger, two-pane settings over the
`settings/<id>` registry, and a third column that keeps a thread open beside the feed.

---

## 11. What this architecture forbids

A short list, so violations are reviewable:

1. A second database, or any schema owned by the web client.
2. Domain logic in the client. If the phone doesn't have the rule, it goes in `services/`.
3. A subdomain origin for the authenticated product (§1).
4. CORS without JSON CSRF in the same diff (§1).
5. A new deep-link path family outside `/pulse/*` and `/search*` (§7.1).
6. Any modification to livestream audio, livestream infrastructure, audio/video calls, call
   audio routing, the Agora session foundation, or the Pulse Radio audio foundation.
   **Inventory only.**
7. An AI provider key in the web bundle. Provider routing is server-side (`undx_router.py`)
   precisely so keys never reach a client — and a web bundle is far easier to inspect than a
   Hermes-compiled binary, so the temptation is higher and the consequence is worse.
8. UNDX write-capable actions on web in phase 1. `APPROVE UNDX WRITE` and
   `APPROVE UNDX GUARD CHANGE` typed into a browser are materially weaker controls than the
   same phrases in an authenticated native app. Read-only surfaces (receipts, run history,
   capabilities) are safe and useful.
9. Flattening the Pulse AI `actions/confirm` + `actions/cancel` pair into an optimistic toast.
   It is a safety control and must be ported as one.
10. Routing around the Private Office `423` lock. The header-bound `X-Office-Grant` /
    `X-Office-Device` handshake must be implemented, not bypassed.

---

## 12. Decisions this document does not make

Owed, with an owner and a deadline, before the phase that depends on each.

| # | Decision | Blocks | Due |
|---:|---|---|---|
| 1 | Notifications lineage: `notifications` (62,279 rows) vs `pulse_notifications` (12,895) | The notification bell | Before the notifications phase |
| 2 | The other 6 duplicate lineages (messages, status, friends, mutes, sellers/orders, rooms) | Each corresponding phase | Per-phase |
| 3 | `user_id = 0` — insert a real system user, backfill, or mandate `LEFT JOIN` | The feed phase **and** the `pulse_posts` FK | Before the feed phase |
| 4 | Web cookie-session revocability — accept the 10-year irrevocable session, or add a server-side session backend (§3.8) | The security model sign-off | Before launch |
| 5 | Is `/api/pulse/communications` (162 routes, no resolvable caller either side) live, dead, or in progress? | Messages architecture | Before the messages phase |
| 6 | Search: Postgres FTS, trigram, or an external index? Search does not exist as a product on **either** platform today — ~160 lines of `LIKE '%x%'` plus 29 lines of Python scoring | The search phase | Before the search phase |
| 7 | Groups: 45 routes, ~0 callers on either client. Is it a live product? | Whether Groups is in scope at all | Before the groups phase |
| 8 | Which ads surface is authoritative — `/api/pulse/ads` (66) or `/api/business-os/advertising` (45)? | The ads phase | Before the ads phase |
| 9 | Deletion of `/api/arena` (157 routes) and the other §6 candidates — **gated on one week of production route-hit logging keyed by client.** Static extraction cannot see a URL assembled at runtime | Cleanup | After logging lands |

**Decision 9 restated as a rule:** the cost of wrongly deleting a live payment or auth route is
not symmetric with the cost of keeping a dead one. Log first.

---

## Cross-references

- `PULSESOC_WEB_API_GAP_ANALYSIS.md` — the 45 shared / 236 native-only endpoint split
- `PULSESOC_DATABASE_GAP_ANALYSIS.md` — the seven lineage decisions and `user_id = 0`
- `PULSESOC_REUSE_VS_REBUILD_MATRIX.md` — what of the existing web survives
- `PULSESOC_NATIVE_TO_WEB_PARITY_MATRIX.md` — capability targets per area
- `PULSESOC_WEB_DESIGN_SYSTEM_MAP.md` §9–11 — tokens, breakpoints, app-promotion styling
- `PULSESOC_BACKEND_ROUTES_AND_AUTH.md` §4, §7 — auth detail behind §3 and §8
- `PULSESOC_WEB_SECURITY_MODEL.md` — session revocability, CSRF, the 0-FK posture
- `PULSESOC_WEB_REBUILD_PHASE_PLAN.md` — when each §8/§9/§12 item is due
