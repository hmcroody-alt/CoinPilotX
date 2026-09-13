# PulseSoc — Backend Inventory

The canonical backend inventory required by the brief. Assembled from two audits produced
separately and preserved here in full:

- **Part I — Routes, auth & authorization.** All 2,146 routes repo-wide, the auth model per
  route family, rate limiting, CSRF/CORS posture.
- **Part II — Services & infrastructure.** The ~707-module service layer, workers and queues,
  realtime infrastructure, the media pipeline, external providers, UNDX, and a product-area
  dependency map.

Generated 2026-09-12 against `main` (`ffd356c5`). Read-only. Claims are cited `file:line`.
`CLAUDE.md` and root `*_REPORT.md` files were **not** trusted as evidence.

**Headline measurements**

| Measure | Value |
|---|---:|
| `bot.py` | 123,710 lines |
| Routes, repo-wide | **2,146** |
| …declared in `bot.py` | 1,777 |
| …declared in Blueprint route packs, outside `bot.py` | **369** (17%) |
| Distinct `/api` rules | **1,331** |
| Non-`/api` rules | 727 |
| HTML-producing page routes | 496 |
| Share of `bot.py` that is HTML/CSS/JS string literal | **46.7%** (~3.70M chars) |
| Service modules | ~707 files, ~272k lines |
| `clean_html()` call sites vs `html.escape` | **2,217** vs **4** |
| Auth decorators (`@login_required` etc.) | **0** — a 3-line preamble repeated 152 times |
| gunicorn topology | 4 workers × 8 threads |

**Three facts that change how this inventory must be read**

1. **`grep '@app.route' bot.py` is wrong by 369 routes.** Blueprint route packs — 162 in
   `pulse_communications_v2/routes.py` alone — make the private-office and marketplace-cart
   families read as non-existent.
2. **f-string route constants mis-record 88 routes** unless module-level constants are
   resolved, which made four *live* marketplace families read as missing.
3. **~382 routes are registered inside `except Exception` blocks.** One broken feature cannot
   block boot — but a subsystem can silently vanish in production. Check boot logs before
   assuming a 404 is a routing bug.

> **Protected systems are inventory only.** `/api/pulse/live/*` and `/api/calls/*` are
> described and counted; no web implementation is proposed for them anywhere in this project.

**Companion documents:** `PULSESOC_WEB_API_GAP_ANALYSIS.md` (the 236-endpoint gap),
`PULSESOC_WEB_SECURITY_MODEL.md`, `PULSESOC_DATABASE_INVENTORY.md`.

---

# Part I — Routes, auth & authorization


READ-ONLY analysis. All line citations are `bot.py:LINE` against the working tree at the
time of writing (`bot.py` = 123,710 lines). Claims that could not be verified in code are
marked **UNVERIFIED**.

Status: COMPLETE.

## 1. Route family analysis

**Corrected baseline: 2,146 routes** — 1,777 in `bot.py`, **369 in blueprint route packs
outside it**. Earlier counts in this project (1,775) and in `CLAUDE.md` (~1,538) both scanned
`bot.py` alone and are therefore wrong. Extraction method, the two traps that produced false
results, and the full blueprint file list are in `PULSESOC_WEB_API_GAP_ANALYSIS.md` §1.

Split: **1,419 `/api` routes** (66%) and **727 non-`/api` routes** (34%).

### 1.1 `/api` families

| Routes | Family | Native callers | Note |
|---:|---|---|---|
| 120 | `/api/arena` | **zero** | CoinPilotX-era legacy. 38 web callers. Largest single deletion candidate |
| 90 | `/api/admin` | zero | Admin console API. Strongest authz in the codebase |
| 66 | `/api/pulse/ads` | 14 | Ads platform; much of it admin/server-side |
| 65 | `/api/pulse/communications` | ~0 | The v2 blueprint. **Status genuinely unclear — see §2** |
| 45 | `/api/business-os/advertising` | zero | Business OS console |
| 45 | `/api/pulse/groups` | ~0 | Groups is largely unwired on **both** clients |
| 38 | `/api/pulse/live` | 17 | **Protected system — inventory only** |
| 32 | `/api/pulse/marketplace` | 19 | Money path. Cart/offers/returns are blueprint-served |
| 30 | `/api/pulse/mobile` | ~0 | **Duplicate of `/api/mobile`.** Two auth surfaces where there should be one |
| 29 | `/api/pulse/messages` | partial | Text messaging |
| 28 | `/api/business-os/marketplace` | zero | Separate from `/api/pulse/marketplace` |
| 26 | `/api/private-office/meetings` | zero | Blueprint; no resolvable caller |
| 25 | `/api/crypto` | partial | Subsystem, still live (see product-areas §5) |
| 24 | `/api/calls` | 3 | **Protected system — inventory only** |
| 21 | `/api/pages` | 16 | Clean JSON CRUD; no web caller at all |
| 21 | `/api/pulse/reels` | 11 | No web caller at all |
| 18 | `/api/messages` | 4 | Media init/upload/complete/download |
| 17 | `/api/business-os/business` | zero | |
| 17 | `/api/business-os/undx` | 10 | Agent permissions, policies, receipts, emergency stop |
| 16 | `/api/account` | 13 | 2FA, trusted devices, session revocation |
| 16 | `/api/pulse-ai` | 4 | Assistant conversation/message/action |
| 15 | `/api/business-os/store`, `/api/pulse/media`, `/api/pulse/comm` | mixed | |
| 13 | `/api/pulse/payments` | 8 | Stripe + Apple IAP verification |
| 12 | `/api/pulse/business`, `/api/mobile/auth`, `/api/reels` | mixed | `/api/reels` is a second reels surface alongside `/api/pulse/reels` |
| 11 | `/api/pulse/posts` | 6 | The single most-shared family between web and native |

**Three duplications visible in this table alone**, each of which a rebuild would otherwise
faithfully reproduce:

1. `/api/mobile/*` **and** `/api/pulse/mobile/*` — the native app calls the former; the latter's
   30 routes have no caller. Two auth surfaces is a security-relevant duplication.
2. `/api/reels` (12) **and** `/api/pulse/reels` (21) — native uses the latter.
3. `/api/pulse/marketplace` (32) **and** `/api/business-os/marketplace` (28) — different
   products sharing a noun, which is worse than a straight duplicate because it reads as one.

### 1.2 Non-`/api` families

| Routes | Family | Note |
|---:|---|---|
| **296** | `/admin` | **41% of all non-`/api` routes.** 188 are inline-HTML pages using neither shared shell |
| 173 | `/pulse` | The actual consumer product surface |
| 45 | `/dashboard` | Account/creator/economy/intelligence dashboards |
| 37 | `/arena` | Legacy, matching the 120 `/api/arena` |
| 27 | `/internal` | Ops endpoints |
| 9 | `/health` | Liveness/readiness |
| ≤6 each | `/education`, `/legal`, `/markets`, `/chat`, `/account`, `/checkout`, `/billing`, `/verify-email`, `/reset-password`, `/scam-shield`, `/webhook(s)`, `/debug`, `/git`, … | |

**The headline for the rebuild:** the admin console is the largest web surface in the
repository — 296 page routes plus 90 API routes — and it is *bigger than the consumer product
it administers* (173 `/pulse` routes). Any plan that treats admin as a final cleanup phase has
mis-scoped roughly a third of the work. It is also the surface with the worst XSS exposure
(`bot.py:28760` renders raw database cells into an admin session with no sanitisation at all)
while simultaneously having the *strongest* authorization model. Those two facts belong in the
same sentence in any planning conversation.

### 1.3 What this means before reading further

- 66% of routes are `/api`, and 872 of those `/api` routes have no statically resolvable caller
  in either client. That is an inference from static analysis, not a deletion list — see
  `PULSESOC_WEB_API_GAP_ANALYSIS.md` §5 for why a dynamic-call audit must come first.
- The consumer product — the thing the native app actually is — is **173 page routes and ~281
  API endpoints**. Everything else is admin, legacy, business console, or unwired.
- That is the real scope of "rebuild the website". It is much smaller than 2,146 suggests, and
  much larger than the 45 shared endpoints suggest.


## 2. Route registration architecture + silent-vanish risk

### 2.1 Two mechanisms, and only one of them can vanish

**Mechanism A — direct decoration on the app object (the 1,775).**
Every route in `/tmp/routes.json` is a literal `@webhook_app.route(...)` in `bot.py`
(first at `bot.py:1489`, last at `bot.py:123686`). `grep -o '^@[A-Za-z_.]*' bot.py`
returns exactly seven distinct decorator kinds across the whole file:

| count | decorator |
|---|---|
| 1777 | `@webhook_app.route` |
| 14 | `@webhook_app.before_request` |
| 4 | `@webhook_app.errorhandler` |
| 4 | `@webhook_app.after_request` |
| 3 | `@media…` (media helper) |
| 2 | `@webhook_app.context_processor` |
| 1 | `@schema_guard.run_once_per_process` |

**There is not a single auth decorator in `bot.py`.** No `@login_required`, no
`@admin_required`, no `@requires_auth`. Every one of the 1,775 in-file routes does its
own auth by *calling a helper inside the view body* (see §4.4). This is the single most
important structural fact for the rebuild: you cannot audit auth coverage by reading
decorators, and nothing enforces that a new route has any auth at all.

These 1,775 routes are registered at import time and are NOT behind any exception
handler — if any one of them raised at decoration time the process would fail to boot.
So `bot.py`'s own surface cannot silently vanish.

**Mechanism B — optional route packs (the swallowed ones).**
`bot.py:1238–1276` defines the pack machinery:

- `ROUTE_PACK_STATUS = {}` (`bot.py:1238`)
- `_record_route_pack(name, register_callable)` (`bot.py:1241`) — calls
  `register_callable(webhook_app)` inside `try/except Exception`; on failure records
  `{"registered": False, "error": <ExcClassName>}` and logs
  `ROUTE_PACK_REGISTRATION_FAILED … every endpoint in this pack will 404` at
  **CRITICAL**.
- `_load_route_pack(name, module_path)` (`bot.py:1257`) — `__import__` is *also* inside
  the try, so an import-time error (a missing service module, a bad env var read at
  module scope, a syntax error) is swallowed identically, logged as
  `ROUTE_PACK_IMPORT_FAILED`.

So CLAUDE.md's claim is **VERIFIED and current** — but with an important correction:
the code has been deliberately hardened since that claim was written. Failures are
logged at CRITICAL (not debug), recorded in a dict, and exposed on a health endpoint.
The comment at `bot.py:1225–1237` states the risk in the same terms as CLAUDE.md.

**Observability that already exists (use it in the rebuild's deploy gate):**
- `GET /health/routes` — `bot.py:121002`; returns `"route_packs": ROUTE_PACK_STATUS`
  (`bot.py:121046`) plus a per-pack boolean map (`bot.py:121041`).
- The main health payload computes
  `route_packs_ok = all(...registered...)` (`bot.py:120950`) and lists the failed pack
  names (`bot.py:120983`).

### 2.2 Every optional registration, and what disappears if it fails

24 packs, all registered at `bot.py:1279–1383`. Route counts are decorator counts in
each pack module.

| # | line | pack | module | routes | URL surface it owns | what disappears |
|---|---|---|---|---|---|---|
| 1 | 1279 | `pulse_communications_v2` | `pulse_communications_v2/routes.py` | **162** | `/api/conversations`, `/api/calls`, `/api/pulse/*`, `/api/pulse-ai`, `/api/admin/calls`, `/health/live*` | **the entire messaging + calling API.** Largest single point of failure in the app. |
| 2 | 1280 | `pulse_presence` | `services/presence_routes.py` | 9 | `/api/pulse/presence*` | online/typing/last-seen |
| 3 | 1281 | `pulse_mobile_settings` | `services/pulse_settings_routes.py` | 12 | `/api/pulse/settings*` | settings, incl. account-deletion lifecycle |
| 4 | 1285 | `pulse_marketplace_cart` | `services/marketplace_cart_routes.py` | 8 | `/api/pulse/marketplace/cart*` | cart / checkout entry |
| 5 | 1286 | `pulse_marketplace_offers` | `services/marketplace_offers_routes.py` | 7 | `/api/pulse/marketplace/offers*` | buyer offers |
| 6 | 1287 | `pulse_marketplace_returns` | `services/marketplace_returns_routes.py` | 6 | `/api/pulse/marketplace/returns*` | returns |
| 7 | 1290 | `business_os_web` | `services/business_os_web.py` | 1 | `/business-os` page | the Business OS web dashboard page |
| 8 | 1296 | `business_os_commerce` | `services/business_os_commerce_routes.py` | 1 + **37** via `add_url_rule` | `/api/business-os/marketplace/*`, `/business-os/commerce` | seller console + 37 commerce endpoints |
| 9 | 1297 | `business_os_suppliers` | `services/business_os_supplier_routes.py` | 10 | `/api/business-os/suppliers*`, `/api/provider-webhooks/*`, `/api/admin/*` | supplier integration **and a provider webhook receiver** |
| 10 | 1302 | `business_os_dropshipping` | `services/business_os_dropshipping_routes.py` | 13 | `/api/business-os/dropshipping*` | dropship import→draft→publish |
| 11 | 1306 | `undx_agent_runs` | `services/undx_agent_run_routes.py` | 2 | `/api/undx/runs*` | run read side |
| 12 | 1311 | `undx_agent_run_control` | `services/undx_agent_run_control_routes.py` | 1 | `/api/undx/runs/<id>/cancel` | run cancel |
| 13 | 1315 | `undx_run_health` | `services/undx_run_health_routes.py` | 1 | `/health/undx/runs` | worker liveness probe |
| 14 | 1323 | `undx_fabric_health` | `services/undx_fabric_health_routes.py` | 1 | `/health/undx/fabric` | provider/spend probe |
| 15 | 1329 | `pulse_market_pulse` | `services/market_pulse_routes.py` | 7 | `/api/pulse/market*` | dashboard market board |
| 16 | 1334 | `private_office` | `services/private_office_routes.py` | 26 | `/api/private-office/*`, `/api/admin/*` | **entitlement truth for Private Office** — if this fails the whole PO tree is undecidable |
| 17 | 1339 | `private_office_structured_records` | `…structured_records_routes.py` | 11 | `/api/private-office/records*` | fact/record store |
| 18 | 1347 | `private_office_documents` | `…documents_routes.py` | 7 | `/api/private-office/documents*` | vault uploads |
| 19 | 1351 | `private_office_relationships` | `…relationships_routes.py` | 5 | `/api/private-office/people*` | relationship graph |
| 20 | 1356 | `private_office_briefings` | `…briefings_routes.py` | 5 | `/api/private-office/briefings*` | briefings |
| 21 | 1360 | `private_office_shield` | `…shield_routes.py` | 4 | `/api/private-office/shield*` | exposure monitoring |
| 22 | 1365 | `private_office_concierge` | `…concierge_routes.py` | 8 | `/api/private-office/concierge*` | concierge desk + operator console |
| 23 | 1371 | `private_office_meetings` | `…meetings_routes.py` | 26 | `/api/private-office/meetings*` | multi-guest meetings |
| 24 | 1379 | `private_office_conversations` | `…conversations_routes.py` | 12 | `/api/private-office/conversations*` | PO messaging view |

### 2.3 Quantified answer

- **0 of the 1,775 `bot.py` routes are behind a swallowed exception.** They are all
  unconditional module-level decorations.
- **≈382 additional routes** (345 decorator routes + 37 `add_url_rule` entries from
  `services/business_os/commerce_gateway.py:ROUTES`) live entirely inside
  `except Exception`-guarded registrations.
- Total mounted surface is therefore roughly **2,157 routes, of which ~17.7% can
  silently disappear** — and the concentration is the problem, not the percentage:
  **pack #1 alone is 162 routes and owns all messaging and calling.**

### 2.4 Second-order silent-vanish risk (feature flags)

Several packs register successfully and then serve 404 by design until an env flag is
set — `business_os_commerce` is documented as "DARK (404) until its `BUSINESS_OS_*`
flag is on" (`bot.py:1293–1295`), `private_office_meetings` is
"Fail-closed behind `PRIVATE_MEETINGS_ENABLED` (default OFF)" (`bot.py:1369`),
`private_office_conversations` behind `PRIVATE_CONVERSATIONS_ENABLED` (`bot.py:1377`).
For a browser client, "registered" and "reachable" are different questions and
`/health/routes` only answers the first.

**Rebuild action:** make `GET /health/routes` a hard deploy gate, and add a flag
snapshot to it.

## 3. The double Flask assignment

**The claim is TRUE but the line numbers in CLAUDE.md are stale, and — as of today —
nothing is actually being lost.**

| claim | reality |
|---|---|
| `webhook_app = Flask(...)` twice | **TRUE** — `bot.py:464` and `bot.py:1213` |
| at ~384 and ~1130 | **STALE** — actual lines are 464 and 1213 |
| second wins | **TRUE** — the name is simply rebound; the first `Flask` object is orphaned |
| "anything attached between those lines is lost" | **TRUE in principle, EMPTY in practice today** |

### What is in the window 464→1213

An exhaustive scan of that window for anything touching the app object
(`awk 'NR>=464 && NR<=1213 && /webhook_app|app\.config|app\.jinja|app\.secret/'`)
returns only **four** lines, and they are all part of the first construction itself:

```
464: webhook_app = Flask(__name__, template_folder="templates", static_folder="static")
465: webhook_app.secret_key = COINPILOTX_SECRET_KEY
466: webhook_app.config.update(   # SESSION_COOKIE_HTTPONLY/SAMESITE/SECURE, lifetime, refresh
473: app = webhook_app
```

`bot.py:1213–1222` is a **byte-identical repeat** of that block. So:

- No `before_request` hook is lost — the first is at `bot.py:2552`, well after 1213.
- No `after_request` hook is lost — first at `bot.py:2618`.
- No `errorhandler` is lost — they are at `bot.py:13007, 13036, 13059, 13121`.
- No `context_processor` is lost — `bot.py:1407, 1466`… wait: `1407` and `1466` are
  **after** 1213, so they attach to the surviving app. Correct.
- No route is lost — the first `@webhook_app.route` is at `bot.py:1489`.
- No config is lost — the config is set identically on both objects.

### What it still costs you

1. **A wasted `Flask` instance** stays alive for the process lifetime (it holds a
   Jinja environment and a static-file handler). Harmless, but it means
   `id(app)` observed before line 1213 differs from the served app — any future
   `from bot import app` executed during that window (e.g. by a module imported at
   `bot.py:~1100`) would capture the **dead** object. Nothing does this today.
2. **It is a live trap for the rebuild.** The natural place to add CORS
   (`CORS(app, ...)`), a CSRF extension, or session-cookie changes for a browser
   client is "next to where the app is configured" — and the first such site you find
   by grepping is line 464, which is the discarded one. Any web-rebuild change made
   there will be silently thrown away, with no error and no test failure.

**Recommendation (not applied — this doc is read-only):** delete `bot.py:464–473`, or
at minimum put a loud comment there. Until then, treat `bot.py:1213` as the only real
app object.

## 4. Authentication

### 4.0 The headline

`bot.py:3658–3659` — the whole identity model in two lines:

```python
def account_user_id():
    return session.get("account_user_id") or account_user_id_from_mobile_access_token() or restore_account_from_persistent_cookie()
```

Three credential sources, tried left to right, first non-falsy wins. This is the root of
the reported bug (§4.3) and it is **still present on `main` today**.

### 4.1 The three resolvers

**(a) Flask session cookie** — `session["account_user_id"]`. Signed with
`COINPILOTX_SECRET_KEY` (`bot.py:134`). Set by the web login flow and, importantly,
**also by `restore_account_from_persistent_cookie()`**, which means a request that
started with only a refresh cookie ends up with a session cookie too.

**(b) Bearer access token** — `account_user_id_from_mobile_access_token()`,
`bot.py:3605–3655`. Fully verified, not just decoded:

1. `Authorization: Bearer <body>.<sig>` (`bot.py:3608–3614`).
2. `sig` = HMAC-SHA256 of `body` under `COINPILOTX_SECRET_KEY`, compared with
   `hmac.compare_digest` (`bot.py:3619–3621`).
3. `body` is urlsafe-b64 JSON `{uid, dh, exp}` (`bot.py:3623–3627`).
4. Rejects `uid <= 0`, empty device hash, or `exp <= now` (`bot.py:3631`).
5. **Database check** — the token hash must match an `active`, non-revoked,
   non-expired row in `mobile_security_sessions` (`bot.py:3635–3645`).
6. The row's `device_hash` must equal the token's `dh` (`bot.py:3651`).
7. Only then: `g.mobile_access_user_id = user_id` (`bot.py:3652`).

This is a genuinely strong design — a stolen token alone is not enough, and revocation is
immediate. **Note the side effect at step 7: `g.mobile_access_user_id` is set _only_ here,
and only when this function actually runs.**

**(c) Long-lived refresh cookie** — `restore_account_from_persistent_cookie()`,
`bot.py:3577–3602`. Reads cookie `PERSISTENT_SESSION_COOKIE` (default
`pulse_refresh_session`, `bot.py:136`), calls `rotate_mobile_refresh_token(...)`, and on
success **writes `session["account_user_id"]` and marks the session permanent**
(`bot.py:3595–3597`). It is skipped for static/health paths and for media-byte delivery
paths (`bot.py:3583–3591`) — that exclusion exists because rotating on every thumbnail
fetch triggered reuse detection and signed users out (`bot.py:3565–3575`).

**Critically: this is a rotating refresh token used as an ordinary browser cookie.** Any
concurrency on the web side (two tabs, a prefetch, a parallel XHR burst) hits the same
reuse-detection path.

### 4.2 Web path vs native path

| | Web (browser) | Native (RN app) |
|---|---|---|
| Primary credential | Flask session cookie | `Authorization: Bearer` |
| Long-lived credential | `pulse_refresh_session` cookie (rotating) | refresh token in expo-secure-store |
| Login endpoint | `POST /login` (form) | `POST /api/mobile/auth/login` (`bot.py:6919`) |
| Refresh | implicit, via `restore_account_from_persistent_cookie()` | `POST /api/mobile/auth/refresh` (`bot.py:6865`) |
| CSRF applicability | **yes — ambient authority** | no — custom header |
| `g.mobile_access_user_id` | never set | **should** be set; often isn't (§4.3) |

There is **no Flask-Login, no Flask-Session, no Flask-JWT** — `grep` for `flask_login`,
`flask_session`, `flask_jwt` in `bot.py` returns nothing. Everything is hand-rolled on top
of Flask's signed-cookie `session`.

The mobile auth surface is at `bot.py:6855–7200` and every endpoint is **double-mounted**
under both `/api/mobile/auth/*` and `/api/pulse/mobile/auth/*` (e.g. `bot.py:7045/7046`,
`7122/7123`). The rebuild must pick one and treat the other as an alias.

### 4.3 The cookie-vs-bearer bug — **STILL PRESENT, VERIFIED**

**Status: NOT FIXED on `main` today.**

`bot.py:3659` is a left-to-right `or` chain. When a request carries **both** a valid
session cookie and a valid bearer token:

1. `session.get("account_user_id")` returns truthy.
2. Python short-circuits — `account_user_id_from_mobile_access_token()` is **never
   called**.
3. Therefore `g.mobile_access_user_id` (set only at `bot.py:3652`) is **never set**.

Reads succeed (identity resolves fine from the cookie). Writes fail, because write gates
test `g.mobile_access_user_id` as their "this is a CSRF-safe native caller" signal and the
native app has no CSRF token to fall back on.

The behaviour is made worse, not better, by `restore_account_from_persistent_cookie()`
(`bot.py:3595`): it *creates* the session cookie for a phone that only sent a refresh
cookie, so a device that started bearer-clean becomes cookie-shadowed on its next request.

**Two independent places in the codebase describe this bug in their own comments**, which
is the strongest possible confirmation that it is live:

- `services/business_os_commerce_routes.py:46–73`, `_verified_bearer_write_authority()`:
  > `g.mobile_access_user_id` is only set when `bot.account_user_id()` actually reaches
  > its bearer branch, and that branch is skipped whenever a session cookie is present.
  > The native app sends both a cookie and a bearer, so the flag stays unset — and the app
  > has no CSRF token to echo, which left every Business OS write refused while every read
  > succeeded.

  Its fix is **local**: it re-runs `bot.account_user_id_from_mobile_access_token()`
  directly, and additionally requires the bearer's user to match the cookie's user
  (`services/business_os_commerce_routes.py:70–72`). That is a good fix — but it protects
  **only the 37 commerce-gateway routes**.

- `bot.py:18415–18429`, `pulse_ads_verify_write()` — carries a comment asserting the
  opposite:
  > The ad endpoints resolve the user before calling this, which is what sets
  > `g.mobile_access_user_id`.

  **That comment is false** for exactly the reason above. `pulse_ads_api_user_required()`
  (`bot.py:18431`) calls `require_account()` → `account_user_id()`, which short-circuits
  on the cookie. So every cookie-bearing native write to the ads API falls through to the
  CSRF branch and fails. This is an unpatched instance of the same bug.

**Blast radius for the rebuild:** any gate that reads `g.mobile_access_user_id` is
affected. Grep that symbol before shipping; today it is trusted in at least
`bot.py:18420` and `services/business_os_commerce_routes.py:83`.

**Why it matters for the web specifically:** the *fix* most people would reach for —
reordering to bearer-first — changes which identity wins when a browser tab and a device
token disagree. The commerce pack's approach (verify both, require them to name the same
user, deny on mismatch) is the correct model and should be lifted into `account_user_id()`
itself rather than re-implemented per pack.

### 4.4 Auth "decorators" — there are none

`grep -oE '^@[A-Za-z_.]*' bot.py | sort | uniq -c` yields only seven decorator kinds
(table in §2.1). **Zero are auth decorators.** All 1,775 in-file routes gate themselves by
calling a helper in the view body. Usage counts (`grep -cE '(^|[^A-Za-z_])NAME\('`):

| helper | line | calls | what it enforces |
|---|---|---|---|
| `api_account_user()` | `bot.py:30784` | **574** | thin wrapper over `require_account()`; returns `None` → caller must emit 401 itself |
| `require_account()` | `bot.py:5461` | 152 | `load_account_by_id(account_user_id())` + `account_login_restriction_message()` ban check; clears session on restriction |
| `load_account_by_id()` | — | 131 | row load, no auth of its own |
| `verify_csrf()` | `bot.py:3237` | 52 | `request.form['csrf_token'] == session['csrf_token']` — **form field only** |
| `admin_current_user()` | `bot.py:14978` | 49 | resolves `session['admin_user_id']` |
| `require_admin_api(perm)` | `bot.py:18397` | 48 | admin session + `admin_has_permission(admin, perm)`; 401/403 tuple |
| `get_csrf_token()` | `bot.py:3229` | 48 | mints/returns session CSRF token |
| `account_user_id()` | `bot.py:3658` | 45 | raw id, **no ban check** |
| `admin_login_required()` | `bot.py:15300` | 38 | admin session resolver (returns a dict, not a decorator, despite the name) |
| `pulse_ads_api_user_required()` | `bot.py:18431` | — | ads-family user gate |
| `pulse_ads_verify_write()` | `bot.py:18415` | — | ads-family CSRF gate (bugged, §4.3) |
| `require_super_user_api/_page()` | `bot.py:5187/5176` | — | super-user gate |
| `api_pro_required()` | `bot.py:5397` | — | paid-tier gate |
| `require_owner_api()` | — | — | owner-only gate (used by entitlement grant/revoke) |
| `_crypto_api_result(..., capability=)` | — | — | crypto-family user + capability gate |
| `undx_kernel_user()` | — | — | UNDX kernel gate |
| `_verified_bearer_write_authority()` | `services/business_os_commerce_routes.py:46` | — | commerce-only bearer re-verify |

**This is the single biggest structural problem for the rebuild.** With at least 17
different gate helpers and no decorator, there is no static way to prove a route is
protected, no way for a linter to require it, and adding a route with no auth at all is
a silent, test-passing change. See §5.3 for the measured coverage attempt.

### 4.5 `before_request` pipeline (14 hooks, all on the surviving app)

In registration order:

| line | hook | relevance to a browser client |
|---|---|---|
| 2552 | `start_performance_trace` | — |
| 2565 | `redirect_www_to_apex_domain` | www → apex 301; matters for cookie domain |
| 2787 | `enforce_https` | — |
| 2805 | `enforce_arena_pro_access` | paywall on `/arena` |
| 2829 | `capture_referral_and_run_trial_maintenance` | — |
| 2884 | `basic_abuse_guard` | rate limit, §6 |
| 2974 | `pulse_security_core_guard` | rate limit + kill switches + **strict JSON field validation** |
| 3064 | `interactive_security_guard` | rate limit + upload size + XSS body scan + extension allowlist |
| 3119 | `log_visitor_request` | off by default (`PULSESOC_VISITOR_LOGGING_ENABLED`) |
| 3177 | `record_pulsesoc_presence_activity` | — |
| 3211 | `enforce_admin_first_password_change` | admin only |
| 3443 | `enforce_admin_form_csrf` | **CSRF — see §4.6** |
| 3483 | `route_app_intent_links_to_the_app_store` | GET redirects to App Store |
| 6835 | `enforce_private_business_os_authentication_boundary` | Private Office / Business OS boundary |

Two of these will bite a web client immediately:

- `pulse_security_core_guard` (`bot.py:3037–3057`) runs
  `pulse_security_core.validate_json_shape(path, payload)` and **rejects any JSON body
  containing an unknown field with a 400** for paths in `STRICT_JSON_FIELDS`. A web client
  that sends one extra field gets a 400 with `security_state: "schema_rejected"`.
- `interactive_security_guard` (`bot.py:3090–3095`) reads the first 6000 bytes of any JSON
  body and 400s on `security_guard.suspicious_text(raw)`. A user posting HTML-looking text
  from a rich-text web editor can trip this.

### 4.6 CSRF — narrow, admin-only, and deliberately exempt for JSON

`get_csrf_token()` (`bot.py:3229`) mints `session['csrf_token']`.
`verify_csrf()` (`bot.py:3237`) checks **only `request.form['csrf_token']`** — it does not
look at headers.

Two hooks provide structural coverage, both admin-scoped:

- `inject_admin_form_csrf` — `@after_request`, `bot.py:3272`. Regex-rewrites the HTML
  response body and injects a hidden `csrf_token` into every `<form method=post>` on any
  `/admin` page (`_ADMIN_POST_FORM_TAG`, `bot.py:3270`). Only for 200/`text/html`.
- `enforce_admin_form_csrf` — `@before_request`, `bot.py:3443`. Rejects the request
  **only if all of**:
  - method in POST/PUT/PATCH/DELETE (`bot.py:3445`), **and**
  - path starts with `/admin` or `/api/admin` (`bot.py:3448`), **and**
  - path not in `CSRF_EXEMPT_ADMIN_PATHS = {"/admin/login","/admin/logout"}`
    (`bot.py:3269, 3450`), **and**
  - `Content-Type` is form-encoded / multipart / text/plain / empty (`bot.py:3457`), **and**
  - `session['admin_user_id']` is present (`bot.py:3459`).

The comment block at `bot.py:3240–3267` is unusually honest and worth reading in full. It
records that an audit found **79 state-changing admin form POSTs, 42 of which never called
`verify_csrf()`**, and 39 of which rendered no token field at all.

**The gap that matters for the rebuild, stated plainly:**

1. **`application/json` is exempt, everywhere, by design** (`bot.py:3451–3456`). The
   stated justification is: *"the browser preflights it cross-site and this app sets no
   CORS headers, so a forged JSON request can never reach these routes."*
   **This reasoning is correct today and becomes false the moment anyone adds CORS.**
   It is a load-bearing dependency between "we have no CORS" and "we have no CSRF on JSON."
   Write it into the rebuild's design docs.
2. **Nothing outside `/admin` and `/api/admin` is covered by the hook at all.** All 426
   `/api/pulse` routes, all 203 `/api/business-os` routes, `/api/account`, `/api/messages`,
   `/api/reels` — **zero blanket CSRF enforcement**. Individual families roll their own:
   `pulse_ads_verify_write()` (`bot.py:18415`), `_csrf_ok()`
   (`services/business_os_commerce_routes.py:77`), `_business_os_ent_csrf_ok()`. These
   three accept an `X-CSRF-Token` / `X-CSRFToken` header; the global `verify_csrf()` does
   not. Two different CSRF contracts coexist.
3. **`SESSION_COOKIE_SAMESITE="Lax"` is doing most of the real work** (`bot.py:1217`), and
   the code says so. Lax blocks cross-site form POSTs in current browsers but not
   same-site subdomain origins, and it is a property of the visitor's browser rather than
   an application control.

### 4.7 Session & cookie configuration

All from `bot.py:1213–1222` (the surviving app) and `bot.py:105–139`:

| setting | value | line | note |
|---|---|---|---|
| `secret_key` | `FLASK_SECRET_KEY` \|\| `SECRET_KEY` \|\| `SESSION_SECRET`, else random | 106, 134 | signs **both** Flask sessions and mobile bearer tokens |
| boot guard | `RuntimeError` if unset in a deployed env | 122–133 | escapable via `PULSESOC_ALLOW_EPHEMERAL_SECRET` |
| `SESSION_COOKIE_HTTPONLY` | `True` | 1216 | good |
| `SESSION_COOKIE_SAMESITE` | `"Lax"` | 1217 | hardcoded, not env-tunable |
| `SESSION_COOKIE_SECURE` | env `SESSION_COOKIE_SECURE`, defaults to "is deployed" | 135, 1218 | |
| `PERMANENT_SESSION_LIFETIME` | `PERSISTENT_SESSION_DAYS` days | 1219 | |
| `PERSISTENT_SESSION_DAYS` | **`max(3650, env)`** | 137 | **a 10-year floor — the env var can only make it longer, never shorter** |
| `SESSION_REFRESH_EACH_REQUEST` | `True` | 1220 | re-emits `Set-Cookie` on every response |
| refresh cookie name | `pulse_refresh_session` | 136 | |
| refresh cookie flags | `httponly=True`, `secure=<same>`, `samesite` env-tunable default `Lax`, `path=/` | 3540–3548, 139 | |
| refresh cookie max-age | `3650 * 86400` | 138 | 10 years |
| refresh reuse grace | 180s, min 30s | 140 | |

Notable findings:

- **The 10-year floor is `max()`, not `min()`** (`bot.py:137`). Setting
  `PULSESOC_PERSISTENT_SESSION_DAYS=30` has no effect. A browser session, once established,
  is effectively permanent. For a web product this is a significant decision that nobody
  can turn down without a code change.
- **One secret signs two very different things.** `COINPILOTX_SECRET_KEY` signs Flask
  session cookies *and* mobile bearer tokens (`bot.py:3619`). Rotating it to invalidate web
  sessions also invalidates every mobile token in the field. There is no key-ID/rotation
  scheme.
- `SESSION_COOKIE_DOMAIN` is **not set** — cookies are host-only on the apex. Combined with
  `redirect_www_to_apex_domain` (`bot.py:2565`) that is coherent, but it means a web
  rebuild cannot live on a subdomain and share the session.

## 5. Authorization

### 5.1 Admin RBAC — actually solid

There are **two entirely separate identity systems**. A user session is
`session['account_user_id']` over the `users` table; an admin session is
`session['admin_user_id']` over the `admin_users` table. **Being a logged-in user grants
no admin capability whatsoever**, and there is no path from one to the other.

`admin_current_user()` (`bot.py:14978`) does more than read the session — it enforces
lifetimes that the 10-year user session does not have:

- `ADMIN_SESSION_ABSOLUTE_HOURS`, default 12 (`bot.py:14948`)
- `ADMIN_SESSION_IDLE_MINUTES`, default 60 (`bot.py:14949`)
- `_admin_session_expired_reason()` (`bot.py:14952`) returns `"legacy_session"` for any
  admin session created before lifetimes existed — i.e. it **fails closed on legacy**.
- On expiry it clears `admin_user_id`, both timestamps, **and `csrf_token`**
  (`bot.py:14986–14989`).
- Failed logins lock the account for 15 minutes after 5 attempts (`bot.py:15345–15350`).

The gate ladder, all at `bot.py:18364–18405`:

| gate | line | enforces | failure |
|---|---|---|---|
| `require_admin_page(permission)` | 18364 | admin session + `admin_has_permission` | redirect to `/admin/login?expired=1` / 403 |
| `require_admin_api(permission="users.view")` | 18397 | same | 401 / 403 JSON |
| `require_owner_api()` | 18375 | `role == "owner"` exactly | 401 / 403 JSON |
| `require_owner_admin_page()` | 18386 | `admin_is_owner_level(admin)` | redirect / 403 |
| `require_owner_account_page()` | 5198 | owner over the **user** identity | — |
| `require_super_user_api/_page()` | 5187 / 5176 | super-user flag | — |

`admin_has_permission(admin, permission)` (`bot.py:~18300`) — owner short-circuits to
`True`; otherwise `ROLE_FALLBACK_PERMISSIONS` (with a `"*"` wildcard), then a 3-way UNION
over `role_permissions`, `admin_role_permissions`, and
`admin_user_roles JOIN admin_role_permissions` (active roles only). **It returns `False`
on exception** — fails closed. Every denial writes `admin_permission_denied` /
`owner_permission_denied` to `admin_audit_logs` (`bot.py:18369, 18381, 18392, 18402`).

**Measured coverage:** of **330** routes under `/admin` or `/api/admin`, a scan of each
view body for any of the gate names above leaves **8** apparent gaps, and all 8 are false
positives on manual inspection:

| line | route | actual gate |
|---|---|---|
| 15089/15090 | `/admin/command-center/account/verification`, `/admin/verification` | `_verification_admin_or_redirect()` |
| 15126 | `POST /api/admin/verification/action` | `_verification_admin_or_redirect()` → 403 |
| 15162, 15182, 15216 | verification badges / appeals / decide | `_verification_admin_or_redirect()` |
| 16066 | `GET /admin/bootstrap-owner` | `secrets.compare_digest(token, ADMIN_BOOTSTRAP_TOKEN)`, 404 if unset (`bot.py:16068–16071`) |
| 28648 | `GET /admin/audit` | pure alias, calls `admin_audit_logs_page()` which gates |

**Verdict: admin gating on the 330 admin routes is effectively 100%.** This is the
best-defended part of the backend.

**One real weakness — `require_admin_password()`, `bot.py:14939`:**

```python
def require_admin_password():
    if session.get("admin_user_id"):
        return True
    expected = os.getenv("ADMIN_ANALYTICS_PASSWORD", "")
    if not expected:
        return False
    supplied = request.args.get("password") or request.headers.get("X-Admin-Password", "")
    return supplied == expected
```

Three problems for a browser client: (1) it accepts the shared password in the **URL query
string**, so it lands in access logs, `Referer` headers, and browser history; (2) the
comparison is `==`, not `secrets.compare_digest` — timing-attackable; (3) it is a single
shared secret with no per-actor identity, so its 9 call sites (`bot.py:14081, 28755,
28824, 28846, 28890, 28928, 28982, 29016, 29033`) produce no attributable audit trail.
This is a role that is not an admin user. **Do not expose these routes to the web
rebuild.**

### 5.2 Object-level / ownership authorization — delegated, and consistently so

The dominant pattern in `bot.py` is: the route authenticates, then **forwards the actor id
into a service function alongside the object id**, and the service does the ownership
predicate. Examples:

- `bot.py:19775` — `pulse_ads_service.get_campaign(conn, user.get("user_id"), campaign_id)`
- `bot.py:19017` — `pulsesoc_pages.set_status(conn, user["user_id"], page_id, ...)`
- `bot.py:88032` — `pulse_delete_post_common(post_id, user)`
- `bot.py:91676` — `messenger_media_foundation.delete_attachment(cur, conn, user, attachment_id)`
- `bot.py:96305` — `pulse_group_remove_member_common(group_id, user, payload)`

This is a good pattern and it is applied with unusual consistency. A scan of all **302**
object-scoped mutating non-admin routes for whether the actor id reaches the body at all
found **23** that appear not to — and every one I inspected manually turned out to forward
a `user` object into a `_common` helper (`bot.py:88025, 91668, 93343–93367, 96297`), i.e.
also false positives.

**I found no route in `bot.py` that mutates a user-owned object without carrying the
actor.** Cross-account isolation in the route layer holds.

**But the honest caveat:** this analysis can only prove the actor *reaches* the service.
It cannot prove the service *uses* it. `services/` is another agent's scope, and the
ownership predicate lives there for essentially every object type in the product. The
rebuild should not treat §5.2 as an isolation guarantee — it is a "the route layer is not
the problem" finding.

Two genuine route-layer notes:

- `bot.py:29824`, `GET|POST /api/undx/desktop-connector/<path:connector_path>` — a
  **server-side proxy to `http://127.0.0.1:8765`** (`UNDX_DESKTOP_CONNECTOR_URL`,
  `bot.py:29809`), gated by `undx_kernel_user()` → `require_super_user_api()`
  (`bot.py:29805`). The `<path:...>` converter is constrained by an allowlist of 10 exact
  paths (`bot.py:29810–29821, 29829`), so it is not an open SSRF — but it is a
  loopback-reaching proxy and must not be exposed to any browser surface.
- `bot.py:29909`, `POST /api/undx/kernel/apply` — writes to the repository, behind
  `require_super_user_api()` plus an in-payload approval phrase and a separate
  `guard_approval` for self-modifying edits (`bot.py:29918–29924`). Same advice.

### 5.3 Private Office — a genuine second lock, and it is header-based

Private Office does not rely on session authority. `services/private_office_routes.py:304`,
`_office_lock_gate(user)`:

- Requires header `X-Office-Grant` (`GRANT_HEADER`, `services/private_office/security.py:117`).
- Also binds `X-Office-Device` (`DEVICE_HEADER`, `…security.py:118`).
- `po_security.validate_grant(cur, user_id, grant_token, session_binding=…,
  device_binding=…)` — the grant is bound to the **credential family that authenticated
  the request**, so it "dies with the session that earned it" and a stolen grant presented
  by another session fails equality (`…security.py:166–190`).
- **Fails closed**: `except Exception → _locked_refusal(...)`, with the comment
  *"a broken lock check is a locked door"* (`…routes.py:325–328`).
- Refusal is a **423 Locked** with `code: "LOCKED"` and `setup_required`
  (`…routes.py:288–301`), carrying no Office data.
- Tier gate (`_gate`, `…routes.py:193`) is separate and additive — "did this member pay
  for the room" vs "did the person holding the phone just prove they are the member".

Also relevant: `enforce_private_business_os_authentication_boundary` (`bot.py:6836`) — a
`before_request` that 401s **every** `/api/business-os/*` path without an authenticated
account (`bot.py:6847–6849`). Note its docstring records that an older construction gate
was *removed*: Business OS is now open to any authenticated account, no flag or allowlist.

**For the web rebuild:** the Office lock is the one place where the backend already
requires headers the browser does not send by default. A web Private Office must
implement grant minting, `X-Office-Grant`/`X-Office-Device` propagation, and the 423
unlock flow — this is real work, not a port.

### 5.4 Summary of authorization posture

| layer | state |
|---|---|
| Admin RBAC | **Strong.** 330/330 gated, per-permission, audited, fails closed, session lifetimes enforced. |
| Owner / super-user tier | Strong. Explicit `role == "owner"` and super-user gates on destructive routes. |
| Private Office second lock | **Strong and unusual.** Header-bound grant, session-bound, fails closed, 423 contract. |
| User→object ownership | Delegated to `services/`; route layer forwards the actor with high consistency. Unverified below the route. |
| Shared admin password | **Weak.** Query-string credential, `==` comparison, no attribution. 9 routes. |
| Authentication→authorization linkage | **No decorators.** Nothing structurally prevents a new route from having neither. |

## 6. Rate limiting

Three independent, uncoordinated limiters run on every request, plus per-family ad-hoc ones.

### 6.1 `basic_abuse_guard` — `bot.py:2885`

- Table: `ABUSE_GUARD_PROTECTED`, `bot.py:2869–2882` — **11 exact paths only**
  (`/login` 12/300s, `/signup` 8/300s, `/forgot-password` 6/300s, `/forgot-username` 6/300s,
  `/api/mobile/auth/recover` and its `/api/pulse/...` twin 6/300s,
  `/api/account/password/change-request` 6/300s, `/admin/login` 8/300s,
  `/create-checkout-session` + `/api/create-checkout-session` 8/300s,
  `/api/ai-assistant` 30/300s).
- Matching is `request.path not in protected` — **exact string equality, no prefix**.
- Keys on `client_ip_hash():path` only — **no user, no device**.
- Store: module-level dict `RATE_LIMIT_BUCKETS` (`bot.py:478`), i.e. **per gunicorn
  worker**. The comment at `bot.py:2906–2917` states the consequence: the table says
  6/300s and production actually permits up to 24 with 4 workers.
- A shared PostgreSQL counter exists (`sentinel_rate_refused`, `bot.py:2928`) but is
  **default OFF** behind `SENTINEL_DISTRIBUTED_LIMITS_MODE`.

### 6.2 `pulse_security_core_guard` — `bot.py:2975`, rules in `services/pulse_security_core.py`

- `HIGH_RISK_RATE_RULES` (`services/pulse_security_core.py:38–58`): 19 prefix rules —
  `/api/mobile/auth/login` 10/300s, `/register` 6/300s, `/recover` 5/600s,
  `/reset-password` 5/600s, `/api/pulse/media/upload` 18/300s, `/api/pulse/reels/create`
  12/300s, `/api/pulse/posts` 30/300s, `/api/pulse/live` 24/300s,
  `/api/pulse/communications` and `/api/pulse/messages` 90/60s, `/api/pulse/ads` 40/300s,
  checkout 8/300s.
- **Catch-all**: any other `/api/*` mutation gets `RateRule(180, 60, "api_mutation")`
  (`services/pulse_security_core.py:138–139`). So *all* API writes are limited, loosely.
- GET is **never** limited here (`rate_rule_for` returns `None` for non-mutating methods,
  `services/pulse_security_core.py:131`).
- **Keys on all three subjects**: `ip:`, `user:` (when resolved), `device:` (when a device
  hash exists) — `services/pulse_security_core.py:147–151`. This is the one limiter that
  is correctly keyed.
- Device hash comes from `pulse_security_core.device_fingerprint(User-Agent,
  X-PulseSoc-Device-Id || X-Device-Id)` (`bot.py:2998–3001`). **A browser sends neither
  header**, so every browser on a given UA collapses to the same device bucket — see §6.5.
- Store is `_RATE_BUCKETS` (process dict) mirrored into `cache_engine`
  (`services/pulse_security_core.py:153–155`). If Redis is configured the mirror is shared;
  otherwise it is per-worker again.

### 6.3 `interactive_security_guard` — `bot.py:3065`

- Rules: `services/security_guard.py:84–98` — `/api/media/upload` 10/300s,
  `/api/arena/roast` 45/60s, `/api/chat/` + `/api/players/` 80/60s, `/api/sms/` 8/600s,
  `/api/telegram/` 12/300s, `/api/alerts` + `/api/auto-signals` 60/60s. POST/PUT/PATCH only.
- Keys on `client_ip_hash():path` only — **no user, no device**.
- Also enforces request-size caps (`bot.py:3080–3089`: 30 MB default, 1024 MB for comms-v2
  attachments, 200 MB for `/api/messages/media/upload`, 150 MB for `/api/pulse/media/upload`),
  a body XSS scan, and a file-extension allowlist.

### 6.4 Family-local limiters

`pulse_ads_rate_limited(bucket, max_hits, window_seconds)` (`bot.py:18455`) keys on
`f"pulse_ads:{bucket}:{user_id or 'anon'}:{client_ip_hash()[:16]}"` — combined user+IP,
the best keying in the file, but only used by the ads family.

### 6.5 Verdict for a browser client

| issue | detail |
|---|---|
| **IP keying behind shared egress** | `client_ip_hash()` (`bot.py:14185`) takes the **first** element of `X-Forwarded-For`. That is the client-supplied leftmost hop. Railway's edge normalises this, but the code itself does no trusted-proxy count. An office/school/mobile-carrier NAT puts hundreds of browser users in one bucket for the two IP-only limiters (§6.1, §6.3) — including `/login` at 12/300s. |
| **Device keying is native-only** | `X-PulseSoc-Device-Id` / `X-Device-Id` (`bot.py:2999–3000`). A browser sends neither, so `device_fingerprint` reduces to the User-Agent. Every Chrome-on-Windows visitor shares a device bucket. For the mutation catch-all (180/60s) that is a real shared ceiling. |
| **Per-worker state** | `RATE_LIMIT_BUCKETS` and `_RATE_BUCKETS` are process dicts. Real limits are `N × WEB_CONCURRENCY` unless Redis is configured for `cache_engine` and the Sentinel distributed mode is on. Both default OFF. |
| **GET is unlimited** | No limiter covers GET except the 11 exact-path rules (which are POST/PUT only anyway). A browser-driven scraper of `/api/pulse/*` reads is unthrottled. |
| **Uncovered mutating surface** | Anything not under `/api/*` and not one of the 11 exact paths has **no rate limit at all** — this includes most of the 281 `/admin` and 160 `/pulse` page POSTs. |

**Rebuild action:** make `X-PulseSoc-Device-Id` a first-class web concept (a stable
per-browser random id in `localStorage`, sent on every request) so the user/device legs of
`pulse_security_core.rate_limited` actually work for browsers, and turn on a shared store
before launch.


## 7. Web-readiness verdict per family

"Web-ready" here means one specific thing: **a browser-based client, authenticated by cookie,
could call this family today without a backend change.** It does not mean the family is well
designed, well tested, or safe to expose — those are separate columns.

Route counts are the corrected repo-wide figures (2,146 routes; 1,331 distinct `/api` rules),
which include the 369 routes declared in blueprint route packs outside `bot.py`. See
`PULSESOC_WEB_API_GAP_ANALYSIS.md` §1.

| Family | `/api` rules | Web callers today | Verdict | Blocking issue |
|---|---:|---:|---|---|
| `/api/pulse` (feed, posts, reels, status, profile, saved) | 406 | partial | 🟡 **Mostly ready** | Media upload needs R2 CORS; reels/status have zero web callers |
| `/api/pulse/marketplace` | ~30 | none | 🟡 **Ready, unexercised** | 19 native-only endpoints; money path, needs its own test pass |
| `/api/pulse/payments` + `/api/premium` | ~13 | none | 🟡 **Ready, unexercised** | Apple IAP verification endpoints are iOS-only by nature; Stripe path is browser-native |
| `/api/mobile/auth` | 20 | **none** | 🔴 **Not ready** | Issues bearer tokens for `expo-secure-store`. A browser needs the cookie leg. See §4 and the note below |
| `/api/account` (2FA, devices, sessions) | 16 | 2 of 16 | 🔴 **Not ready** | 13 native-only. Re-auth flow assumes a native prompt |
| `/api/pages` | 21 | **none** | 🟢 **Ready** | Plain JSON CRUD, cookie-auth compatible. Largest clean win in the gap |
| `/api/messages` + `/api/chat` | 24 | partial | 🟡 **Ready for text, not for media** | Media init/upload/complete is native-shaped; realtime is polling only (see below) |
| `/api/pulse/communications` (v2 blueprint) | 53 | none | ⚪ **Unknown** | 162-route blueprint with no resolvable caller either side. Establish status before building on it |
| `/api/pulse/live` | ~33 | none | ⛔ **Out of scope** | **Protected system. Inventory only.** |
| `/api/calls` | 24 | none | ⛔ **Out of scope** | **Protected system. Inventory only.** |
| `/api/business-os` | 174 | 12 web-only | 🟡 **Ready** | Admin/console surface; separate authz model |
| `/api/business-os/undx` + `/api/undx` + `/api/pulse-ai` | ~28 | none | 🟡 **Ready, needs guardrails** | Approval-phrase gates (`APPROVE UNDX WRITE`, `APPROVE UNDX GUARD CHANGE`) must not be exposed to a browser without the confirmation UX |
| `/api/private-office` | 61 | none | 🟢 **Ready** | Blueprint-served JSON. Has a second, header-bound lock that already fails closed |
| `/api/admin` | 90 | 17 | 🟢 **Ready** | RBAC is the strongest authz in the codebase (330/330 gated). But see the shared-password caveat |
| `/api/arena` | 120 | 38 | ⚫ **Legacy** | CoinPilotX-era. **Zero native callers.** Deletion candidate pending a dynamic-call audit |
| `/api/crypto`, `/api/alerts`, `/api/portfolio` | ~55 | partial | 🟡 **Ready** | Subsystem, not core product |
| `/api/progress`, `/api/education`, `/api/rewards` | ~22 | none | 🟢 **Ready** | Simple read-mostly JSON |
| `/api/push` | 8 | 2 | 🟡 **Partial** | Web push (VAPID) exists and works; APNs/FCM legs are native-only by definition |

**Legend:** 🟢 ready · 🟡 ready with a named caveat · 🔴 needs backend work · ⚪ status unknown ·
⛔ protected, excluded · ⚫ legacy

### 7.1 The four findings that actually constrain the rebuild

**1. Auth is the only genuinely blocking backend gap.**
Everything else is either ready or merely unexercised. `/login` already mints both a session
cookie *and* native bearer/refresh tokens in one handler, so the primitives exist — but there
is no decorator anywhere. `grep` for `@login_required`, `@require_login`, `@admin_required`
returns **zero matches** across the repo. The gate is a copy-pasted three-line preamble calling
`require_account()`, repeated **152 times**. Nothing structurally prevents a new route from
having no gate at all, and a new web client is exactly the moment that bites. **A decorator with
a default-deny registration check is the single highest-leverage backend change in this project.**

**2. Realtime is polling, by deliberate decision — not by omission.**
`bot.py:89367` states it plainly: *"Long-lived browser streams can exhaust the main Gunicorn
worker pool."* Both SSE routes return `204` with `X-Pulse-Realtime-Transport: polling`. There is
no WebSocket server. Presence, typing indicators and read receipts all poll. The gunicorn config
(4 workers × 8 threads) forbids the architecture a modern web client would otherwise assume.
This is a constraint to design within, not a bug to fix mid-rebuild — changing it means changing
the deployment topology.

**3. Rate limiting is effectively off for browsers.**
The user/device legs of `pulse_security_core.rate_limited` key on `X-PulseSoc-Device-Id`, which
no browser sends. A web client that does not adopt a stable per-browser id inherits IP-only
limiting — and behind Railway's edge that is a much coarser bucket than intended. The limiter
state also lives in a per-process dict, so it is per-gunicorn-worker; with 4 workers the
effective limit is ~4× the configured one. Both need fixing *before* a second client multiplies
the traffic, not after.

**4. One secret signs two different things.**
`COINPILOTX_SECRET_KEY` (`bot.py:3619`) signs Flask session cookies *and* mobile bearer tokens,
with no key-ID or rotation scheme. Rotating it to invalidate compromised web sessions logs out
every phone in the field simultaneously. Adding a third client to that single key increases the
blast radius again. Split the signing keys before launch, not after an incident.

### 7.2 One deployment fact that shapes the whole architecture

`SESSION_COOKIE_DOMAIN` is **not set**, so cookies are host-only on the apex, and
`redirect_www_to_apex_domain` (`bot.py:2565`) reinforces that. This is internally coherent, but
it has a hard consequence:

> **A rebuilt web client cannot live on a subdomain and share the session.** No
> `app.pulsesoc.com` or `new.pulsesoc.com` staging origin will be authenticated. Either the
> rebuild is served from the apex path-wise alongside the existing site, or
> `SESSION_COOKIE_DOMAIN` changes — and that change invalidates every live web session on
> deploy.

This also interacts with the iOS Associated Domains entitlement, which claims only
`pulsesoc.com`. Both constraints point the same direction: **the rebuild ships on the apex
origin.** The phased plan in `PULSESOC_WEB_REBUILD_PHASE_PLAN.md` is built around that.


---

# Part II — Services & infrastructure


> Scope: service layer, workers/queues, realtime infra, media pipeline, external
> providers, UNDX, other subsystems, and a product-area dependency map.
> **Out of scope** (owned by another analysis): routes, auth, authorization,
> rate limiting, CORS/CSRF.
>
> READ-ONLY analysis. Nothing in the audio/live/call foundation was modified.
> Claims are cited `file:line`. Anything not confirmed from source is marked
> **UNVERIFIED**. `CLAUDE.md` and root `*_REPORT.md` files were NOT trusted.
>
> Generated 2026-09-12 against `main`.

## Contents
1. [Service layer census](#1-service-layer-census)
2. [Background workers and queues](#2-background-workers-and-queues)
3. [Realtime infrastructure](#3-realtime-infrastructure-inventory-only)
4. [Media pipeline](#4-media-pipeline)
5. [External providers](#5-external-providers)
6. [UNDX layer](#6-undx-layer)
7. [Other subsystems](#7-other-subsystems)
8. [Dependency map](#8-dependency-map)

---

## 1. Service layer census

### 1.0 Reconciliation

`ls services/*.py | wc -l` = **344**. That is **343 domain modules + `services/__init__.py`**.
All 343 are classified below; the per-domain counts sum to 343.

**The 344 undercounts the service layer by ~4x.** There are also **10 sub-packages**
holding **364 further `.py` files / ~129,300 lines**:

| Package | Files | Lines | Owns |
|---|---:|---:|---|
| `services/business_os/` | 204 | 65,881 | Business OS: commerce, suppliers/CJ dropshipping, advertising selection, events/ticketing, insights |
| `services/private_office/` | 40 | 29,584 | Private Office: meetings, conversations, documents, relationships, feature matrix |
| `services/sentinel/` | 56 | 12,653 | Security sentinel: rate limiting, providers, runtime |
| `services/undx_brain/` | 20 | 12,408 | UNDX brain: config + reasoning core |
| `services/command_center_worker/` | 13 | 3,553 | The (undeployed) realtime worker — see §3.2 |
| `services/pulse_briefings/` | 5 | 2,097 | Scheduled briefings |
| `services/pulse_ai/` | 9 | 1,515 | Pulse AI helpers |
| `services/intelligence_collectors/` | 11 | 1,465 | Intelligence ingestion |
| `services/providers/` | 6 | 140 | Provider adapters |

Top-level modules total **143,150 lines**. **Whole service layer ≈ 707 files, ≈272,000
lines** — roughly 2.2x `bot.py` itself.

### 1.1 Domains (343 top-level modules)

| # | Domain | Modules | Lines | Owns |
|---:|---|---:|---:|---|
| 1 | **UNDX / AI agent layer** | 50 | 39,109 | Mission/agent runtime, tool gateway, capability registry, knowledge map, cost, policy, privacy, health, canary/shadow, verification |
| 2 | **AI / ML (non-UNDX)** | 24 | 4,619 | `pulse_ai_service` (2,140) + `pulse_ai_provider_router` (642) + `pulse_ai_web_search`; the other 21 are 12–120-line shells |
| 3 | **Live / streaming / RTC** | 27 | 2,822 | Agora push + cloud recording, Mux live, participants, distribution, archive, health, discovery, restream |
| 4 | **Marketplace / commerce / dropship** | 27 | 10,397 | Cart, offers, returns, variants, listing lifecycle, reservations (policy/schema/sweeper/reconciler), settlement, fulfillment, payouts, seller identity/money, Business OS route packs |
| 5 | **Advertising** | 11 | 11,213 | `pulse_ads_service`, ads OS, adsets, audiences, insights, library, reporting, advertiser portal, ad payments, worker service, policy engine |
| 6 | **Arena / games** | 19 | 1,545 | Arena world/events/replay/victory, roast battle, crowd energy, leaderboards |
| 7 | **Crypto / market data** | 25 | 11,901 | `alert_engine` (4,277 — largest service in the repo), CoinGecko client, market intelligence + portfolio, observations, pulse, predictions, news, wallet |
| 8 | **Private office** | 9 | 5,063 | The route surface; the real logic is in `services/private_office/` (29,584 lines) |
| 9 | **Messaging / chat** | 7 | 5,364 | `pulsesoc_communications_engine` (2,330), messenger media foundation, chat bridge, chat health, `command_center_client` |
| 10 | **Media / upload / storage** | 11 | 3,487 | `media_service` (1,365), `media_storage` (R2), upload sessions, upload progress, covers, previews, music, saved content |
| 11 | **Notifications / push** | 9 | 7,950 | `pulsesoc_notification_system` (2,979), `notification_service` (2,306), `push_service` (1,285), email/SMS/Brevo, orchestrator, health |
| 12 | **Feed / ranking / discovery** | 11 | 3,935 | `pulse_feed_engine` (3,077) is effectively the whole domain; ranking/personalization modules are 12–51-line stubs |
| 13 | **Search / SEO** | 3 | 160 | `pulse_search_engine` is **29 lines** — see §7.1 |
| 14 | **Social graph / profile / identity** | 15 | 3,256 | `presence_service` (1,113), presence routes, social graph, profile, viewer permissions, premium identity |
| 15 | **Auth / security / moderation** | 16 | 1,449 | `scam_shield` (412), `pulse_security_core` (267), moderation engine, privilege, safe execution (+ `services/sentinel/` 12,653) |
| 16 | **Payments / premium / monetization** | 18 | 4,038 | `premium_entitlement_service` (1,108), Apple IAP credits + server API, payment router, payment provider, Stripe webhook verification, treasury |
| 17 | **Analytics / intelligence** | 23 | 15,446 | Eight `dashboard_*_command_center` modules, `pulsesoc_intelligence_engine` (3,081), dashboard centers, growth, promotions, mission control |
| 18 | **Realtime transport** | 6 | 791 | `realtime_engine`, `realtime_service`, `websocket_orchestrator`, `event_bus_engine`, `distributed_realtime_engine` — see §3.2 |
| 19 | **i18n / translation** | 2 | 1,056 | `content_translation` (782) + `translation_providers` (274) |
| 20 | **Admin / ops / reliability** | 23 | 4,441 | `app_links` (910), `backend_management_registry` (877), admin gateway, feature flags, schema guard, device classification, Telegram router |
| 21 | **Infra / db / util** | 7 | 5,103 | `db` (1,097), `cache_engine`, `pulsesoc_pages` (2,044), `pulse_settings_routes` (1,227), region prefs, mutation audit |
| | **Total** | **343** | **143,150** | |

Largest single modules: `alert_engine` 4,277 · `undx_agent_runtime` 4,234 ·
`undx_knowledge_map` 3,234 · `undx_agent_tools` 3,171 · `pulsesoc_intelligence_engine`
3,081 · `pulse_feed_engine` 3,077 · `pulsesoc_notification_system` 2,979.

### 1.2 Dead modules

Import graph built by parsing `from services import …` (incl. parenthesised multi-line
forms), `from services.X`, `import services.X`, and intra-package relative imports across
every `.py` in the repo except `mobile/` and `mobile-native/`. Then corrected for
**dynamic registration**: `bot.py:1257` defines `_load_route_pack(name, module_path)` and
`bot.py:1279-1380` loads ~24 route packs **by string path** (e.g.
`_load_route_pack("pulse_presence", "services.presence_routes")` at `bot.py:1280`), which
a naive import scan reports as dead. All `*_routes` modules are live via this mechanism.

**19 genuinely dead modules** (zero importers, not a route pack):

`admin_service`(27) · `ai_memory_engine`(12) · `alert_service`(9) · `analytics_service`(22) ·
`audit_service`(32) · `billing_service`(37) · `leaderboard_engine`(26) ·
`live_network_engine`(27) · `market_service`(13) · `news_intel`(6) · `portfolio_intel`(9) ·
`recovery_service`(13) · `security_service`(20) · `seo_service`(30) · `stripe_service`(11) ·
`telegram_service`(13) · `undx_model_audit`(276) · `user_service`(24) · `wallet_service`(5)

**The pattern is unmistakable:** the plain `<noun>_service.py` names — `user_service`,
`stripe_service`, `wallet_service`, `billing_service`, `analytics_service`,
`audit_service`, `security_service`, `alert_service`, `market_service`, `admin_service`,
`telegram_service`, `seo_service` — are **abandoned first-generation shells**, all under
40 lines, superseded by `pulse_*` / `pulsesoc_*` successors. `undx_model_audit` (276
lines) is the only substantial dead module. Total dead code: **585 lines, 0.4%** — small.

**The real waste is elsewhere.** Roughly **60 modules are live-but-vestigial**: imported
somewhere but under ~40 lines and clearly placeholder (`civilization_ai_engine` 18,
`world_simulation_engine` 12, `identity_sovereignty_engine` 17, `decentralized_trust_engine`
10, `global_social_energy_engine` 12, `ui_state_engine` 12, `self_healing_engine` 15,
`intelligence_products_engine` 12, `marketplace_engine` 12, `scam_shield_service` 5 …).
For the rebuild, treat any `*_engine.py` under 50 lines as **aspirational naming, not
capability** — verify before depending on it.

**20 modules are reachable only from tests or `scripts/`** after excluding route packs:
`ai_service`, `pulse_apple_server_api`, `undx_canary`, `undx_routing_evidence`,
`undx_shadow`. (`undx_shadow` in particular is referenced by memory notes as a live
safety gate — its only *import* sites are tests, so it is presumably invoked via the
UNDX tool gateway by name. **UNVERIFIED**.)

## 2. Background workers and queues

### 2.1 The real Procfile

`Procfile:1-6` — **six process types: `web` + five workers.** CLAUDE.md's claim of
"gunicorn `web` plus `undx_worker` and `email_worker`" is **stale/wrong**.

| Procfile entry | Command | File |
|---|---|---|
| `web` | `gunicorn bot:app --workers ${WEB_CONCURRENCY:-4} --threads ${WEB_THREADS:-8} --timeout 120` | `bot.py` |
| `undx_worker` | `python undx_worker.py` | `undx_worker.py` |
| `email_worker` | `python email_worker.py` | `email_worker.py` |
| `ads_worker` | `python pulse_ads_worker.py` | `pulse_ads_worker.py` |
| `alert_worker` | `python alert_worker.py` | `alert_worker.py` |
| `media_worker` | `python media_worker.py` | `media_worker.py` |

Note the web process is **threaded gunicorn (sync workers + 8 threads)**, not gevent/
eventlet. That is a hard constraint for Section 3: a long-lived WebSocket per client
would consume one of 4x8 = 32 threads. This is the single most important infrastructure
fact for the web rebuild.

### 2.2 Worker-by-worker

| Worker | Lines | Deployed? | What it does | Consumes |
|---|---|---|---|---|
| `undx_worker.py` | 141 | **yes** | Keeps the UNDX Intelligence Router warm, reports provider-config status, drives `undx_mission_runtime` / `undx_agent_runs`. Explicitly documents that it "does not call providers, read files, run commands, or execute repository actions" (`undx_worker.py:3-6`). | UNDX run/mission tables |
| `email_worker.py` | 45 | **yes** | Drains the email outbox by calling `bot.process_email_delivery_jobs(limit=batch)` on a 10s loop (`email_worker.py:33`). Sets `COINPILOTX_INIT_DB_ON_IMPORT=0` and `EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED=0` before importing `bot` (`email_worker.py:11-12`) — i.e. the web tier ALSO opportunistically sends email unless disabled. | DB outbox (`email_delivery_jobs` / `failed_email_queue`) |
| `pulse_ads_worker.py` | 163 | **yes** (as `ads_worker`) | Five cadences in one process: drain `pulse_ad_jobs` every cycle (~20s), campaign ops sweep, attribution every ~300s, billing reconciliation every ~600s, reporting precompute every ~300s (`pulse_ads_worker.py:8-19`). Explicitly **never charges wallets** — the synchronous delivery path owns money. Delegates to `services/pulse_ads_worker_service.py`. | `pulse_ad_jobs` DB table |
| `alert_worker.py` | 143 | **yes** | Crypto/market alerts: `alert_engine`, `auto_signals_service`, `live_market_service`, `market_observations`, `pulse_briefings` (`alert_worker.py:18-20`). Samples an 80-coin board sharing `live_market_service.get_crypto_quote`'s 45s cache (`alert_worker.py:36-39`). | `alert_delivery_jobs`, CoinGecko |
| `media_worker.py` | 1000 | **yes** | Validation, thumbnail fallbacks, media queue jobs, ffmpeg shell-outs, R2 writes, heartbeats (`media_worker.py:1-9`, `:35-37`). The heavyweight worker. | media job table + R2 + ffmpeg |
| `pulse_worker.py` | 296 | **ORPHANED** | Pulse feed background jobs (`pulse_ai`, `pulse_feed_engine`) **and** hosts the marketplace reservation sweep via `marketplace_reservation_sweeper` on a 1–5 min cadence (`pulse_worker.py:10-12`, `:19-44`). | `pulse_jobs`, reservation rows |
| `supplier_worker.py` | 43 | **ORPHANED** (and disabled-by-default) | CJ dropshipping reconciliation tick; gated on `CJ_RECONCILIATION_ENABLED` and `policy.require_network()` (`supplier_worker.py:16-19`). Swallows exception text deliberately because credentials are request-local (`supplier_worker.py:25-26`). | `business_os_supplier_sync_jobs` |
| `telegram_worker.py` | 15 | **ORPHANED** | Thin shim: `bot.main()` long-polls Telegram (`telegram_worker.py:9-11`). | Telegram getUpdates |

**Three orphans.** The most consequential is `pulse_worker.py`: **marketplace stock
reservations are never swept in production** because the only process that hosts the
sweeper is not in the Procfile. Feed precompute jobs (`pulse_jobs`) likewise have no
deployed drainer. This is a live correctness gap, not a rebuild concern —
flagged, **UNVERIFIED** whether Railway defines these services outside the Procfile
(Railway can override start commands per service in its dashboard).

### 2.3 The queue mechanism

**There is no real queue broker. Every queue is a database table polled on a sleep
loop.** Evidence: each worker's main loop is `while RUNNING: ...; time.sleep(interval)`
with a `limit=batch_size` SELECT (e.g. `email_worker.py:31-41`, `pulse_ads_worker.py:35-40`).

Redis is **optional and is a cache/presence accelerator, not a broker**:
`services/cache_engine.py:1-4` — "Optional Redis-backed cache and presence helpers…
Redis becomes an accelerator, not a hard dependency", with an in-memory TTL fallback
when `REDIS_URL` is unset (`services/cache_engine.py:27-39`). Redis appears in ~20
modules including `services/sentinel/rate_limit.py` and the
`services/command_center_worker/` package (`redis_manager.py`, `presence.py`,
`messaging.py`, `realtime_transport.py`, `notifications.py`) — that package is the one
place Redis is used pub/sub-style.

Job tables observed by reference frequency: `pulse_jobs` (65), `push_delivery_jobs` (47),
`failed_email_queue` (43), `alert_delivery_jobs` (43), `moderation_queue` (35),
`payout_queue` (26), `notification_delivery_jobs` (21), `refund_queue` (20),
`pulse_ad_jobs` (18), `intelligence_delivery_jobs` (18), `content_queue` (18),
`business_os_supplier_sync_jobs` (13), `pulse_ad_moderation_queue` (12).

**Web-rebuild impact:** the web client depends on `media_worker` (uploads complete
asynchronously — the browser must poll or be pushed a completion signal),
`email_worker` (verification/transactional mail), `ads_worker` (advertiser dashboards),
and `undx_worker` (AI runs). It does **not** depend on `alert_worker` unless crypto
alerts ship on web. A DB-polling queue with ~20s cadence means the web UI must be
designed for eventual consistency on uploads, ad stats, and AI runs.

## 3. Realtime infrastructure (inventory only)

> **No audio / live / call code was modified.** This section is an inventory.

### 3.1 The RTC provider is **Agora**, not LiveKit

CLAUDE.md is **stale**. Evidence, on `main` today:

| Evidence | Verdict |
|---|---|
| `mobile-native/package.json:71` — `"react-native-agora": "4.6.2"` | Agora is the installed client SDK |
| No `livekit` string anywhere in `mobile-native/package.json` or `package-lock.json` | **no LiveKit client package at all** |
| `requirements.txt:19` — `agora-token-builder==1.0.0`; no livekit server SDK | Agora server-side token minting |
| `.env.example:1168-1177` — `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, `AGORA_REST_CUSTOMER_ID`, `AGORA_REST_CUSTOMER_SECRET`, `AGORA_TOKEN_TTL_SECONDS`, `AGORA_RECORDING_TIMEOUT_SECONDS`, `AGORA_MEDIA_PUSH_*`. **No `LIVEKIT_*` key exists.** | Agora is the configured provider |
| `services/agora_media_push_service.py:1` — "Server-only Agora Media Push bridge for PulseSoc Live -> Mux"; `services/agora_cloud_recording_service.py` | Agora → Mux is the live pipeline |
| `bot.py:49214` renders `<label>Agora channel<code>{livekit_room}</code></label>` | **the smoking gun**: the UI label says Agora while the Python variable is still named `livekit_room` |

**Conclusion:** the product migrated LiveKit → Agora and left the *vocabulary* behind.
`livekit_*` survives as **string literals in state machines and telemetry**
(`bot.py:48335-48348` `livekit_live_states`, `bot.py:50312-50318` cohost step names,
`bot.py:47678` error codes `LIVEKIT_ROOM_JOIN_FAILED` / `LIVEKIT_PUBLISH_FAILED`) and in
module names (`services/live_distribution_service.py`, `services/live_participants.py`).
Those are **persisted enum values, not a live integration** — a web rebuild must keep
emitting/accepting them but must not conclude LiveKit is in play. Backend modules
mentioning "livekit" (8) are all doing state/health string comparison; the only modules
that *call* a provider are the two `agora_*` services plus `services/mux_live_service.py`.

### 3.2 Realtime messaging: **it is polling, deliberately**

SSE exists in only three places in `bot.py` and **two of the three are disabled by
default behind env flags**:

- `bot.py:89360` `GET /api/pulse/live/stream` — the main Pulse event stream. Gated twice:
  `PULSE_MAIN_APP_SSE_ALLOWED` and `PULSE_LEGACY_SSE_ENABLED`. When off it returns
  **204** with headers `X-Pulse-Realtime-Transport: polling` and
  `X-Pulse-SSE-Disabled-Reason: main_app_worker_protection`. The inline comment
  (`bot.py:89367-89369`) states the reason outright: *"Long-lived browser streams can
  exhaust the main Gunicorn worker pool. The main app stays on fast polling unless a
  dedicated realtime service is explicitly enabled and capacity-tested."*
- `bot.py:36592` `GET /api/arena/realtime/<kind>/<id>/stream` — gated on `ARENA_SSE_ENABLED`,
  same 204 + `X-Pulse-Realtime-Transport: polling` fallback. When enabled it is a
  **bounded** stream: 10 iterations × 2s sleep, then a heartbeat and close
  (`bot.py:36601-36612`). It is polling wearing an SSE costume.
- `services/command_center_worker/app.py` — the only unconditional SSE, and it lives in a
  **separate Flask app** that is not in the Procfile.

The canonical transport is therefore **HTTP long-poll/short-poll against DB-backed
cursors**: `GET /api/arena/realtime/<kind>/<id>?after_id=N` (`bot.py:36576`) and
`GET /api/pulse/messages/<conversation_id>` (`bot.py:90998`) with `since_id`/`after_id`
paging. `services/realtime_service.py` is the shared layer
(`poll_arena_channel`, `publish_arena_event`, `realtime_manager.heartbeat`).

**There is no WebSocket server.** `services/websocket_orchestrator.py`,
`services/realtime_engine.py` and `command_center_worker/realtime_transport.py` exist,
but the last one says so explicitly (`realtime_transport.py:1-6`): *"intentionally
dependency-light… keeps an in-process replay buffer and connection registry today, while
preserving the API shape needed for Redis/WebSocket fanout later."* An in-process registry
inside a 4-worker gunicorn deployment cannot fan out — **UNVERIFIED** whether any of these
are reachable in production; none is in the Procfile.

### 3.3 Presence, typing, read receipts

| Signal | Write endpoint | Read endpoint | Transport |
|---|---|---|---|
| Presence (online) | `POST /api/pulse/presence` heartbeat → `pulse_mark_online()` (`bot.py:89289-89292`) | `GET /api/pulse/messages/<id>/presence` (`bot.py:90931`), `GET /api/world-presence` (`bot.py:31469`), `GET /api/arena/presence` (`bot.py:34902`) | poll |
| Typing | `POST /api/pulse/typing` (`bot.py:89301`), `POST /api/arena/chat/typing` (`bot.py:36912`) | folded into the conversation/presence poll | poll |
| Read receipts | `GET /api/business-os/messages/threads/<id>/read` (`bot.py:25270`); message-level read state in the Pulse message payload | poll |

The Command Center worker defines a richer event vocabulary that the main app does not
use: `presence_updated`, `message_created`, `message_delivered`, `message_read`,
`typing_started`, `typing_stopped`, `unread_count_updated`
(`services/command_center_worker/realtime_transport.py:23-30`). Treat this package as the
**intended future transport**, currently dormant.

Redis backs presence when `REDIS_URL` is set (`services/cache_engine.py`,
`command_center_worker/presence.py`), degrading to per-process memory otherwise — meaning
presence is **per-gunicorn-worker inconsistent** without Redis.

### 3.4 Livestreaming pipeline

`Agora (ingest/RTC) → Agora Media Push (REST) → Mux (RTMP ingest, HLS playback, recording)`

- `services/agora_media_push_service.py` — server-only bridge, pushes the Agora channel to
  a Mux RTMP endpoint. Config check at `:15-26`.
- `services/agora_cloud_recording_service.py` — Agora Cloud Recording.
- `services/mux_live_service.py` — Mux live stream create/get/disable; `.env.example:459-479`
  gives per-call timeouts, `MUX_TOKEN_ID`/`MUX_TOKEN_SECRET`, `MUX_WEBHOOK_SECRET`, and
  `MUX_SIGNING_KEY_ID`/`MUX_SIGNING_PRIVATE_KEY` for **signed playback URLs**.
- `services/live_stream_health_service.py`, `services/live_distribution_service.py`,
  `services/live_participants.py`, `services/live_archive_service.py` — health, fanout,
  participant roster, VOD archive.
- Fallback path: when Mux egress quota is exhausted the stream flips to `livekit_direct` /
  `browser_live_livekit_direct` (`bot.py:49155`) — legacy naming for **direct Agora
  playback without Mux**. `bot.py:51458` `POST /api/pulse/live/<id>/browser-publish`
  already exists for a browser publisher.

### 3.5 What a WEB client needs per realtime system

| System | Native today | Web requirement | Gap |
|---|---|---|---|
| Voice/video calls | `react-native-agora` 4.6.2 | **`agora-rtc-sdk-ng`** (Agora Web SDK). Token minting already server-side via `agora-token-builder`; the web client needs a token endpoint returning appId + channel + RTC token + uid. | Ship the JS SDK; **do not** touch the native audio foundation. Web and native can share one Agora channel — same appId/certificate. |
| Live viewing | Mux HLS | `hls.js` or native `<video>` on Safari, plus signed playback tokens from `MUX_SIGNING_KEY_ID` | Low risk — already just a URL |
| Live broadcasting from browser | n/a | Agora Web SDK publish → existing `POST /api/pulse/live/<id>/browser-publish` (`bot.py:51458`) | Endpoint exists; **UNVERIFIED** how complete |
| Messaging | poll | Same polling API works unchanged in a browser. | None. But a browser tab polling 1–2s multiplies request volume against a 32-thread gunicorn pool. |
| Presence/typing/receipts | poll | Same. | Needs Redis to be consistent across workers |
| Push/notification badge | FCM/APNs | Web Push (VAPID) — see §5 | Separate fan-out path |
| SSE (if ever enabled) | flagged off | Browsers cap **6 concurrent connections per origin**; an SSE connection eats one. And the gunicorn comment already rules it out. | Recommend: keep polling, or stand up the Command Center worker as a real service |

**The decisive constraint for the rebuild:** the backend is a threaded-sync gunicorn app
that has explicitly rejected long-lived connections to protect its worker pool. A web
client should assume **polling parity with native**, not upgrade to sockets, unless
`services/command_center_worker/` is promoted to a deployed process with its own dyno and
a Redis pub/sub fanout.

## 4. Media pipeline

### 4.1 Two upload paths coexist

**Path A — classic multipart POST to Flask** (the majority). 11 endpoints:

| Route | `bot.py` line |
|---|---|
| `POST /api/media/upload` | 103071 |
| `POST /api/pulse/media/upload` | 103481 |
| `POST /api/pulse/messages/upload`, `/api/pulse/messages/media/upload` | 91240–91241 |
| `POST /api/messages/media/init` → `/upload` → `/complete` → `/attach` | 91488, 91504, 91529, 91545 |
| `POST /api/pulse/marketplace/media/upload`, `/digital-files/upload` | 94377, 94556 |
| `POST /api/pulse/ads/accounts/<id>/media/upload` | 19426 |
| `POST /api/pulse/music/upload`, `/api/pulse/reels/sounds/upload` | 43223, 87048 |
| `POST /api/pulse/teachers/documents/upload` | 95092 |

**Path B — presigned direct-to-R2 with multipart** (`services/media_upload_sessions.py`,
"Authorized direct-to-object-storage upload sessions for PulseSoc media", `:1`):

```
POST /api/pulse/media/uploads                      -> create session   (bot.py:103087)
GET  /api/pulse/media/uploads/<upload_id>           -> status           (103097)
POST /api/pulse/media/uploads/<id>/parts/sign       -> presigned parts  (103109)
POST /api/pulse/media/uploads/<id>/refresh          -> re-sign          (103120)
POST /api/pulse/media/uploads/<id>/complete         -> S3 CompleteMPU   (103130)
POST /api/pulse/media/uploads/<id>/finalize         -> register media   (103141)
POST /api/pulse/media/uploads/<id>/abort            -> cleanup          (103151)
```

Session state lives in table `pulse_media_upload_sessions`
(`services/media_upload_sessions.py:44+`). Tunables (`:19-24`): TTL 3600s, signed-URL TTL
**clamped to ≤900s** regardless of env, multipart threshold 16 MB, part size ≥5 MB
(S3 minimum), **max 12 parts signed per request** — so a large video needs repeated
`/parts/sign` calls, which the web client must implement.

**Path C — Mux direct upload** for video: `POST /api/pulse/media/mux/direct-upload`
(103186) and `.../complete` (103281). Mux issues its own upload URL; the browser PUTs
straight to Mux.

### 4.2 Storage backend

`services/media_storage.py:1` — "Media storage abstraction for local dev and R2/S3
production URLs". Provider chosen by `MEDIA_STORAGE_PROVIDER` (`:20`), default `local`.
Credential lookup accepts **both** `R2_*` and `AWS_*`/`S3_*` aliases, and the file carries
a comment about a real incident where the admin panel reported `configured=False` on a
working `S3_BUCKET`/`AWS_*` deployment (`:27-30`). Public URLs are built from
`R2_PUBLIC_BASE_URL` (`.env.example:37` → `https://cdn.coinpilotx.app`, still the old
brand). Filenames go through `werkzeug.utils.secure_filename` + a random token
(`media_storage.py:58-60`). Private media has a separate root
(`PRIVATE_MEDIA_UPLOAD_DIR`, default `instance/private_uploads`, `:15`) and is served
through `GET /api/messages/media/<id>/download` (91612) and `/access` (91576) rather than
a public URL.

### 4.3 Validation and limits (`.env.example:584-601, 1019, 1190-1194`)

| Setting | Default |
|---|---|
| `MEDIA_UPLOAD_ALLOWED_TYPES` | `jpg,jpeg,png,webp,gif,mp4,webm,mov,mp3,m4a,aac,wav,ogg,pdf,txt,doc,docx` |
| `MEDIA_UPLOAD_MAX_IMAGE_MB` / `MAX_FILE_MB` / `MAX_UPLOAD_MB` | 12 |
| `MEDIA_UPLOAD_MAX_GIF_MB` | 8 |
| `MEDIA_UPLOAD_MAX_AUDIO_MB` | 15 |
| `MEDIA_UPLOAD_MAX_VIDEO_MB` | 150 |
| `MEDIA_UPLOAD_MAX_STATUS_VIDEO_MB` | 350 |
| `MEDIA_DIRECT_UPLOAD_MAX_VIDEO_GB` | 25 (Path B/C only) |
| `MEDIA_REQUIRE_DURABLE_UPLOAD` | unset |

Note the **12 MB Flask-POST ceiling vs 25 GB direct-upload ceiling** — a 200x gap. Any web
feature handling video *must* use Path B or C.

### 4.4 Processing (media_worker)

`media_worker.py:78` — `MEDIA_JOB_TYPES = {"generate_thumbnail", "process_video",
"finalize_live_replay"}`. The worker shells out to `ffmpeg` (`:114-121`,
`MEDIA_WORKER_FFMPEG_CRF=23`, `MEDIA_WORKER_FFMPEG_PRESET=veryfast`,
`MEDIA_WORKER_TRANSCODE_TIMEOUT_SECONDS=180`). Batch 25, interval 20s, max 3 attempts
(`.env.example:593-601`).

**It degrades rather than fails when ffmpeg is absent** (`media_worker.py:160-161`:
`MEDIA_ENGINE_FFMPEG_MISSING thumbnails/transcoding will use safe fallbacks`). The
fallback is to set `thumbnail_url = media_url` (`:218-221`) — i.e. a video's "thumbnail"
becomes the video file itself. Browsers will download the whole video to render a grid
cell. **This is a real web-rebuild hazard**: the native app tolerates it; a reels grid in a
browser will not.

### 4.5 Traced end-to-end: publishing a Reel

`POST /api/pulse/reels/create` (alias `POST /api/reels`) — `bot.py:87094-87160`:

1. Client has **already uploaded** — the route takes `media_ids`, not a file
   (`bot.py:87108-87118`). Reels are a **two-phase publish**.
2. Looks up `chat_media_uploads` by `id` + `uploader_user_id`, excluding
   `moderation_status='blocked'` (`:87124-87131`). Note the table name: **all media,
   including reels and posts, lands in `chat_media_uploads`.**
3. `media_service.resolve_media(row)` produces `playback_url`, `media_url`,
   `mux_playback_id`, `mux_status`, `processing_status` (`:87136`).
4. Rejects non-video with "Reels require an MP4, MOV, or WEBM video" (`:87139`).
5. If a Mux playback id exists but `mux_status` is not in `{ready, asset_ready,
   available}`, `processing_status` is forced to `mux_processing` (`:87144-87147`) — the
   reel is created in a **pending** state and the client must poll.
   `POST /api/pulse/videos/<id>/retry` (86604) exists for stuck assets.
6. Optional music: `music_service.attach_music_payload()` with an `is_creator_safe` gate
   (`:87155-87159`).

Full path: `presign → PUT to R2 (or Mux direct) → finalize → row in chat_media_uploads →
media_worker job (thumbnail/transcode) → Mux asset ready → POST /api/pulse/reels/create →
poll until processing_status=ready`.

### 4.6 What changes for browser uploads

| Concern | Native today | Web requirement |
|---|---|---|
| File selection | native picker, SDK gives a path + known size | `<input type=file>` / drag-drop; `File.size` is available so the presign flow works |
| **CORS on direct-to-R2** | irrelevant — RN issues the PUT outside a browser origin | **Blocking.** The R2 bucket needs a CORS policy allowing `PUT` + `Origin: https://pulsesoc.com` and exposing `ETag` (required to complete a multipart upload). No CORS config was found in the repo — it is bucket-side. **UNVERIFIED, and the single most likely thing to break first.** |
| Multipart part signing | same API | Browser must chunk with `Blob.slice()`, honour the **12-parts-per-sign** cap (`media_upload_sessions.py:24`), and re-sign as the ≤900s URL TTL expires mid-upload on slow connections |
| Progress | native SDK callbacks | `XMLHttpRequest.upload.onprogress` (note: `fetch()` has **no** upload progress). Server-side `services/upload_progress_service.py` + `GET /api/pulse/media/uploads/<id>` give a polled fallback |
| Resume | session TTL 3600s | Persist `upload_id` + completed part ETags in `localStorage`; `/refresh` re-signs |
| Thumbnails | worker + native player | Needs the ffmpeg path to actually work (§4.4), or generate a client-side poster from a `<canvas>` frame |
| Video playback | Mux HLS via native player | `hls.js`; Safari plays HLS natively. Signed playback needs `MUX_SIGNING_KEY_ID`/`MUX_SIGNING_PRIVATE_KEY` tokens minted server-side (`.env.example:478-479`) |
| Large files | 25 GB allowed | Browser memory: never read the file whole; slice only |
| Private media | `/access` + `/download` | Same endpoints; must not be `<img src>`-able without the access check |

## 5. External providers

### 5.1 Env surface

`.env.example` documents **650 keys** (`grep -cE '^[A-Za-z0-9_]+=' .env.example`).
CLAUDE.md says "~180" — **stale by 3.6x**. Largest prefixes: `PULSE_` 49, `UNDX_` 45,
`PULSESOC_` 40, `BUSINESS_` 29, `MEDIA_` 26, `MUX_` 17, `BREVO_` 17, `TRANSLATION_` 16,
`COINGECKO_` 16, `GOOGLE_` 15, `PRIVATE_` 14, `STRIPE_` 13, `SENTINEL_` 11, `PUSH_` 11,
`CJ_` 11, `R2_` 9, `AGORA_` 8, `APPLE_` 7, `REALTIME_` 7, `LIVESTREAM_` 7.

### 5.2 Providers

| Provider | Purpose | Integration lives in | Keys | Web client needs it? |
|---|---|---|---|---|
| **Stripe** | Subscriptions, marketplace settlement, ad wallet top-ups, payouts | `services/payment_provider.py`, `services/pulse_payment_router.py`, `services/stripe_webhook_verification.py`, `services/marketplace_settlement_service.py`, `seller_money`. (`services/stripe_service.py` is **dead**, §1.2) | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`, `STRIPE_PRO_LINK` server-side; **`STRIPE_PUBLISHABLE_KEY` (`.env.example:28`) is publishable by design** | **Yes** — Stripe.js / Elements with the publishable key. Everything else stays server-side. |
| **Agora** | Voice/video RTC, cloud recording, media push to Mux | `services/agora_media_push_service.py`, `services/agora_cloud_recording_service.py`, `requirements.txt:19` `agora-token-builder` | `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, `AGORA_REST_CUSTOMER_ID/SECRET` (`.env.example:1168-1177`) | **Yes** — Agora Web SDK. `APP_ID` is public; the **certificate must never leave the server** — tokens are minted server-side. |
| **Mux** | Live ingest/HLS, VOD assets, direct uploads, signed playback, Mux Data | `services/mux_live_service.py`, `services/live_archive_service.py`, `media_service` | `MUX_TOKEN_ID`, `MUX_TOKEN_SECRET`, `MUX_WEBHOOK_SECRET`, `MUX_SIGNING_KEY_ID`, `MUX_SIGNING_PRIVATE_KEY` — all server-only. `MUX_DATA_ENV_KEY` is client-safe. | **Yes** for playback URLs + direct-upload URLs; obtains both from our API. |
| **Cloudflare R2** (boto3/S3) | Object storage + CDN | `services/media_storage.py`, `services/media_upload_sessions.py` | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT`/`R2_ACCOUNT_ID`, `R2_PUBLIC_BASE_URL`; `AWS_*`/`S3_*` accepted as aliases | Indirectly — presigned URLs only. **Requires bucket CORS** (§4.6). |
| **Brevo** | Transactional email + SMS + contact lists | `services/email_service.py`, `services/sms_service.py`, `services/brevo_contacts.py` | `BREVO_API_KEY`, `BREVO_SMS_API_KEY`, sender/list IDs (`.env.example:64-72, 338-342`) | No — server-side only, via `email_worker`. |
| **Firebase / FCM** | Android + web push | `services/push_service.py`, `services/native_push_readiness.py` | `FCM_PROJECT_ID`, `FCM_CLIENT_EMAIL`, `FCM_PRIVATE_KEY`, `FCM_SERVER_KEY` (`.env.example:351-354`) | Only if web push goes through FCM; VAPID is the simpler path. |
| **APNs** | iOS push | `services/push_service.py` | `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_PRIVATE_KEY`, `APNS_BUNDLE_ID`, `APNS_USE_SANDBOX` (`:355-359`) | No — native only. |
| **Web Push (VAPID)** | Browser push | `services/push_service.py` | `WEB_PUSH_PUBLIC_KEY`/`PRIVATE_KEY`/`SUBJECT` (`:345-347`) **and** a duplicate `VAPID_PUBLIC_KEY`/`PRIVATE_KEY`/`SUBJECT` (`:348-350`) | **Yes, and it already exists.** Two key pairs for one mechanism — confirm which `push_service` actually reads before wiring a service worker. |
| **Google Cloud Translation** | i18n | `services/translation_providers.py`, `services/content_translation.py` | `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON`, `..._API_KEY`, `..._LOCATION` (`:410-413`) | No — server-side. |
| **Telegram** | Bot: commands, typed questions, alert delivery | `services/telegram_text_router.py`, `bot.main()` via `telegram_worker.py` | `TELEGRAM_BOT_TOKEN` (`:487`) | No. And the worker is **not deployed** (§2.2). |
| **CoinGecko** | Crypto prices/history/news | `services/coingecko_client.py`, `services/live_market_service.py`, `market_data` | 16 keys incl. a **budget guard**: `COINGECKO_MONTHLY_CREDIT`, `_BUDGET_WARN/HIGH/PROTECT`, `_PROTECTIVE_TTL_MULTIPLIER` (`:287-328`) — TTLs stretch automatically as credit depletes | No — server-side, cached. |
| **Redis** | Optional cache + presence (+ pub/sub in the undeployed Command Center worker) | `services/cache_engine.py`, `services/sentinel/rate_limit.py`, `services/command_center_worker/redis_manager.py` | `REDIS_URL` | No, but see §3.3 — without it presence is per-worker. |
| **Apple IAP** | In-app purchases / credits | `services/pulse_apple_iap_credits.py`, `services/pulse_apple_server_api.py` | `APPLE_*` (7 keys) | No — and a **web store cannot use it**, so entitlement parity needs Stripe. |
| **CJ Dropshipping** | Supplier catalogue + fulfilment | `services/business_os/suppliers/*`, `supplier_worker.py` | 11 `CJ_*` keys; gated on `CJ_RECONCILIATION_ENABLED` | No. |
| **AI providers** | See §6 | `undx_router.py`, `services/pulse_ai_provider_router.py` | `OPENAI_API_KEY`, `CLAUDE_AI_API`, `Gemini_AI_API`, `DEEPSEEK_AI_API`, `GROQ_AI_API`, `META_MODEL_API_KEY`, `PERPLEXITY_API_KEY` | **No — and that is a stated invariant** (`undx_router.py:3-4`). |

**Only three provider values are ever legitimately client-visible:**
`STRIPE_PUBLISHABLE_KEY`, `AGORA_APP_ID`, `MUX_DATA_ENV_KEY` (plus the VAPID *public*
key). Everything else must stay behind the API in the web build exactly as it does in the
native build.

## 6. UNDX layer

UNDX is the largest single subsystem in the repo: **50 top-level services (39,109 lines) +
`services/undx_brain/` (20 files, 12,408) + 5 root modules (4,541)** ≈ **56,000 lines**.

### 6.1 Root modules

| Module | Lines | Role |
|---|---:|---|
| `undx_router.py` | 1,849 | Provider selection + failover for all UNDX chat |
| `undx_desktop_connector.py` | 1,171 | **A separate Flask app** meant to run on a local Mac, giving UNDX scoped filesystem access |
| `undx_execution_kernel.py` | 845 | Proposes and applies diffs against the repo |
| `undx_brain_layer.py` | 535 | Shared reasoning front door: mission classification, file selection, multi-agent review, safety |
| `undx_worker.py` | 141 | Deployed worker (§2.2) |

### 6.2 API surface — 29 routes

Two clusters:

- **Core** (`bot.py:29726-29956`): `POST /api/undx/chat`, `GET|POST /api/undx/agent-council`,
  `GET|POST /api/undx/desktop-connector/<path:connector_path>` (a proxy), and the kernel:
  `/api/undx/kernel/scan`, `/propose`, `/apply`, `/validate`, `/git`. Plus
  `GET /health/undx` (121057) and pages `/pulse/undx/actions` (55824),
  `/pulse/premium/undx` (56546).
- **Business OS UNDX** (`bot.py:26808-27047`): policies, requests, tools, permissions,
  confirmations, receipts, **emergency-stop**, decisions, action-center, and an
  agentic marketplace flow (`listings/draft` → `publish/plan` → `publish/execute`).
  This is a *separate, permission-gated* agent surface — note the plan/execute split and
  the explicit confirmation + receipt endpoints.

### 6.3 Provider routing

`undx_router.py:1-4`: *"Server-side provider selection for UNDX chat. The router never
exposes API keys to the browser and keeps OpenAI as the final fallback provider."*

**Seven providers**, not five (`undx_router.py:90-170`):

| Provider | Key env | Default model | Kill switch | JSON enforcement |
|---|---|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | `UNDX_OPENAI_ENABLED` | `json_object` |
| Claude | `CLAUDE_AI_API` | `claude-haiku-4-5` | `UNDX_CLAUDE_ENABLED` | **none** — Messages API 400s on `response_format` |
| Gemini | `Gemini_AI_API` | `gemini-flash-lite-latest` | — | `response_mime_type` |
| DeepSeek | `DEEPSEEK_AI_API` | `deepseek-chat` | — | unknown (account returns 402) |
| Groq | `GROQ_AI_API` | `llama-3.1-8b-instant` | — | unknown |
| **Meta Muse** | `META_MODEL_API_KEY` | `muse-spark-1.3` | `META_MUSE_ENABLED` | `json_object` |
| **Perplexity** | `PERPLEXITY_API_KEY` | `sonar` | `UNDX_PERPLEXITY_ENABLED` | `json_schema` |

Design notes worth carrying into a web surface:

- **Structured output is a dialect, not a boolean** (`undx_router.py:44-52`): three vendors
  spell "must be JSON" three ways; Perplexity 400s on `json_object` but honours
  `json_schema`. Capability values were established by a live probe
  (`scripts/undx_structured_output_capability_probe.py`), not from docs.
- **A key alone is not consent** — per-provider `enable_env` kill switches exist so a
  provider can leave rotation without deleting the credential (`undx_router.py:62-64`).
- Per-provider timeouts and `reasoning_overhead_tokens` (Meta: 3,000) because reasoning
  tokens come out of the same `max_tokens` budget.
- **Known-broken today, per the source's own comments:** `GROQ_AI_API` holds a multi-line
  JSON document rather than a bare key, so `Bearer <value>` is an illegal header and every
  Groq attempt "spends a slot and a breaker increment on a request that was never sent"
  (`undx_router.py:~145`). DeepSeek returns 402 Insufficient Balance. Two of seven
  providers are dead weight in the failover chain.
- Supporting services: `undx_cost`, `undx_health`, `undx_privacy`, `undx_call_domain`
  (imported at `undx_router.py:20`), plus `undx_call_guard` which wraps `requests`/`urllib`
  **per interpreter** to count unrouted provider calls (`undx_worker.py:22-28`).
- A `COUNCIL_AGENT_PROVIDER_MAP` (`undx_router.py:172+`) maps named agents
  (e.g. "Architect Agent") to providers — that is what `/api/undx/agent-council` serves.

### 6.4 Security model — verified, and stronger than described

`undx_execution_kernel.py`:

- `APPROVAL_PHRASE = "APPROVE UNDX WRITE"` (`:26`) — **confirmed**.
- `PROTECTED_PATTERNS` (`:33-51`) — **confirmed and broader than the CLAUDE.md summary**:
  `.env`, `.env.`, `.git/`, `__pycache__/`, `venv/`, `.venv/`, `credentials`, `credential`,
  `secret`, `token`, `private_key`, `id_rsa`, `.pem`, `.key`, `.sqlite`, `.sqlite3`,
  `coinpilotx.db`.
- Repository containment to `DEFAULT_REPOSITORY_PATH` (`:25`) — note this is a
  **hardcoded absolute path** `/Users/hmcherie/Desktop/CoinPilotX`, which will not exist on
  Railway. The kernel is a local-operator tool, not a production capability.
- Backups to `.undx_backups`, audit log `undx_execution_log.jsonl` (`:27-28`).
- **Undocumented and important: a second approval tier.** `SELF_GOVERNING_PATTERNS`
  (`:66-73`) covers `undx_execution_kernel.py`, `undx_agent_policy.py`,
  `tests/protection/`, `scripts/protection/`, `.github/workflows/`, and
  `config/realtime-audio-protected-paths.json`; writes to those require
  `GUARD_APPROVAL_PHRASE = "APPROVE UNDX GUARD CHANGE"` (`:78`). The rationale in the
  source (`:56-65`) is precise: a single approved "routine refactor" of the kernel could
  empty `PROTECTED_PATTERNS` or blank the approval phrase, so guard changes escalate
  rather than being banned outright.
- `undx_brain_layer.py:18-33` — `PLANNING_ONLY_PHRASES` ("proposal only", "do not write",
  "analysis", …) force a mission into plan-only mode; `undx_execution_kernel.py:534`
  raises `KernelError("Brain Layer blocked legacy HTML generation for a planning-only
  mission.")`.
- `undx_desktop_connector.py:1-6` — "blocks protected paths, refuses path traversal,
  writes only approved proposal files, and runs only allowlisted validation/Git commands."

### 6.5 What a web UNDX surface needs

1. **Chat** — `POST /api/undx/chat` is a plain request/response JSON route, so it works in
   a browser today. But **there is no streaming**: no SSE/chunked token stream exists for
   UNDX, and §3.2 explains why one would be unwelcome on this gunicorn config. A web UNDX
   chat will feel slower than users expect unless the Command Center worker is stood up to
   host the stream.
2. **Agent council** — `/api/undx/agent-council` returns multi-agent output; a web UI
   needs per-agent progress, which again argues for streaming.
3. **Kernel routes must not be exposed to the public web.** `/api/undx/kernel/apply` and
   `/git` write to a hardcoded local path. Keep them on an operator/admin surface only.
   (Authorization is the other agent's scope; flagging the surface here.)
4. **Business OS UNDX** is the agentic surface that *should* be on the web: it already has
   the right shape — policies, permissions, confirmations, receipts, plan/execute split,
   and an emergency stop. A web client needs UI for `action-center`, `confirmations`, and
   `emergency-stop`.
5. **No keys client-side** — the router's stated invariant. A web UNDX surface must call
   our API, never a provider directly.

## 7. Other subsystems

### 7.1 Search — there is no search engine

**No Postgres FTS, no trigram, no external engine.** `grep` for `to_tsvector`, `tsquery`,
`pg_trgm`, `similarity(`, `fts5` across `bot.py` and `services/` returns **nothing**.

`GET /api/pulse/search` (`bot.py:41673`) is `LIKE ?` against
`pulse_posts.title/body/ai_tags_json/tags_json/post_type` and `pulse_comments.body`
(`bot.py:41709-41748`). `services/pulse_search_engine.py` is **29 lines** of in-Python
scoring: regex tokenise, `+8` per token found in a concatenated haystack, plus
`trust*0.18 + freshness*0.1`.

Search surfaces: `/api/pulse/search` (41673), `/api/pulse/users/search` (89881),
`/api/messages/users/search` (89882), `/api/pulse/messages/search` (92132),
`/api/pulse/marketplace/search` (54361), `/api/pulse/music/search` (42916),
`/api/crypto/assets/search` (8100), plus web pages `/search` (1996), `/pulse/search` (42041).

**Rebuild implication:** a web search UX (instant, typo-tolerant, faceted) has **no
backend to build on**. Leading-wildcard `LIKE '%term%'` cannot use an index, so this
degrades with table size. Either accept native-parity (exact-substring, small result sets)
or fund Postgres FTS/`pg_trgm` as a separate workstream. This is the largest capability gap
found.

### 7.2 Ranking and recommendations

`services/pulse_feed_engine.py` (3,077 lines) **is** the feed. The modules that sound like
ranking are stubs: `pulse_feed_ranking_engine` 51 lines, `reel_ranking_engine` 51,
`feed_ai_personalization` 26, `personalization_matrix` 31, `neural_discovery_engine` 13,
`social_loop_engine` 29. Real supporting logic: `feed_intelligence_service` (242),
`content_graph_intelligence_service` (332), `discovery_visibility` (65),
`live_ranking_engine` (64). Precompute jobs go to `pulse_jobs` — **drained only by the
orphaned `pulse_worker.py`** (§2.2).

### 7.3 Moderation and safety

`services/pulse_moderation_engine.py` (102), `services/ai_moderation_core.py` (33),
`services/scam_shield.py` (412) + `scam_shield_engine` (46), `pulse_ai_safety` (105),
`roast_safety_filter` (25), `services/sentinel/` (56 files, 12,653 lines — the real
security engine), `services/pulse_security_core.py` (267). Queues: `moderation_queue`,
`pulse_ad_moderation_queue`, `content_queue`, `review_queue`. Media carries
`moderation_status` on `chat_media_uploads` and is filtered at read time (`bot.py:87128`).
Admin surfaces: `/admin/media-moderation` (28210), `/admin/security-events` (28553).

### 7.4 Marketplace / commerce

27 top-level modules (10,397 lines) + `services/business_os/` (204 files, 65,881). The
reservation subsystem is the interesting part: `marketplace_reservation_policy` /
`_schema` / `_sweeper` / `_reconciler`. `pulse_worker.py:19-44` documents that the sweeper
is the single implementation of release and that the worker only provides scheduling — but
**that worker is not deployed**. Settlement: `marketplace_settlement_service`,
`seller_money`, `marketplace_payout_scheduler`, queues `payout_queue`, `refund_queue`,
`chargeback_queue`. Dropshipping: `services/business_os/suppliers/*` + CJ.

### 7.5 Payments / premium

`premium_entitlement_service` (1,108) is the entitlement authority.
`pulse_payment_router` + `payment_provider` abstract Stripe;
`stripe_webhook_verification` (171) validates webhooks. Two purchase rails:
**Stripe (web/native)** and **Apple IAP** (`pulse_apple_iap_credits` 446,
`pulse_apple_server_api` 217). A web store cannot use IAP — entitlements granted via
Apple must still resolve on web, so the web build depends on
`premium_entitlement_service` being rail-agnostic. Gates: `pro_access`,
`premium_capability_engine`, `premium_crypto_access`, `premium_visibility_engine`.

### 7.6 Notifications and push fan-out

`pulsesoc_notification_system` (2,979) + `notification_service` (2,306) +
`push_service` (1,285) + `notification_orchestrator` (152) + `notification_health_engine`
(201). Entry point `push_service.send_push(user_id, title, body, data, push_type)`
(`services/push_service.py:879`). Transports: APNs, FCM, **Web Push/VAPID (already
present)**. Queues: `push_delivery_jobs` (47 refs), `notification_delivery_jobs` (21),
`delivery_jobs`, `intelligence_delivery_jobs`. Email/SMS via Brevo through
`failed_email_queue` + the deployed `email_worker`.

**Web needs:** a service worker + `pushManager.subscribe()` with the VAPID public key, and
a device-registration call to whatever endpoint `push_service` reads. The server side
already exists; only the browser registration path is missing. **UNVERIFIED** whether the
existing device-token table accepts a web-push subscription shape.

### 7.7 Private Office

9 route modules (5,063 lines) + `services/private_office/` (40 files, 29,584). Sub-domains:
briefings, concierge, conversations, documents, meetings, relationships, shield, structured
records. All registered as route packs (`bot.py:1335-1380`). `private_office/meetings.py`
and `conversations.py` both reference Agora — meetings are RTC calls, so Private Office on
web inherits the §3.5 Agora Web SDK requirement. `PRIVATE_` has 14 env keys.

### 7.8 Market intelligence / crypto

`alert_engine` (4,277 — largest service), `market_intelligence` (1,406) + `_alerts` +
`_portfolio`, `market_observations` (734), `portfolio_service` (716),
`coingecko_client` (634) with a 16-key budget guard, `market_pulse`, `auto_signals_service`,
`crypto_alert_conditions`, `predictions_service`. Driven by the deployed `alert_worker`.
Legacy in origin (this repo began as CoinPilotX) but **actively deployed**, not dead.

### 7.9 Analytics / dashboards

23 modules, 15,446 lines. Eight `dashboard_*_command_center` modules (account, ads, ai,
creator, crypto, economy, intelligence, network) plus `pulsesoc_dashboard_centers` (1,520),
`pulse_dashboard_mission_control` (860), `pulsesoc_intelligence_engine` (3,081),
`pulsesoc_growth_engine` (800), `retention_analytics`, `conversion_funnel_engine`,
`event_stream_analytics`. These are **server-rendered admin surfaces today** — the single
biggest chunk of "website that is not the app".

### 7.10 Audit logging

`admin_audit_logs` is the shared table (`services/admin_gateway.py:145-175`), used for
access logging, login throttling and lockout. `services/audit_service.py` writes to it but
is **dead** (§1.2) — the live writer is `admin_gateway`. Per-feature audit tables are
declared in `services/backend_management_registry.py:49-70`
(`profile_audit_logs`, `account_audit_logs`, …). Also `services/pulse_mutation_audit.py`
and `/api/account/security-events` (84341).

### 7.11 Schema scale

`bot.py` contains **547 `CREATE TABLE IF NOT EXISTS` statements**, and more DDL lives in
`services/*/ensure_schema()` functions. CLAUDE.md's "~170 tables in `AUTO_PK_TABLES`" counts
one list, not the schema. There is no migration framework — schema is imperative and must be
idempotent. (Prior session notes put production at 884 tables; **UNVERIFIED here**, but 547
in `bot.py` alone is consistent with that and inconsistent with 170.)

## 8. Dependency map

Table names below were extracted from the 547 `CREATE TABLE IF NOT EXISTS` statements in
`bot.py` (511 distinct names); more are created by `ensure_schema()` in service modules.
Route line numbers are `bot.py` unless noted.

| Product area | Key routes | Services | DB tables | External | Workers |
|---|---|---|---|---|---|
| **Auth / identity** | `/api/mobile/auth/*`, `/api/account/*`, `/api/account/security-events` (84341) | `auth_service`*, `user_context`, `pulse_id_service`, `pulse_identity_engine`, `premium_identity_engine`, `services/sentinel/`, `pulse_security_core`, `admin_gateway` | `email_verification_tokens`, `password_reset_tokens`, `account_recovery_tokens`, `mobile_security_sessions`, `user_trusted_devices`, `admin_audit_logs`, `blocked_users` | Brevo (verification mail) | `email_worker` |
| **Feed** | `/api/pulse/feed*`, `/pulse` | `pulse_feed_engine` (3,077), `feed_intelligence_service`, `content_graph_intelligence_service`, `discovery_visibility`, `pulse_ai` | `pulse_posts`, `pulse_jobs`, `pulse_follows` | — | `pulse_worker` **(orphaned)** |
| **Posts** | `/api/pulse/posts*` | `pulse_feed_engine`, `media_service`, `pulse_moderation_engine`, `saved_content_service` | `pulse_posts`, `chat_media_uploads`, `moderation_queue` | R2, Mux | `media_worker` |
| **Comments** | `/api/pulse/reels/<id>/comments` (87423), `/api/pulse/videos/<id>/comments` (86361) | `pulse_feed_engine`, `pulse_moderation_engine` | `pulse_comments` | — | — |
| **Reels** | `POST /api/reels` \| `/api/pulse/reels/create` (87094), `/api/pulse/reels/feed` (86914), react/view/save/share/pin (87287–87855), `/api/pulse/reels/sounds/*` (86940, 87014, 87048) | `pulse_feed_engine`, `reel_ranking_engine`†, `media_service`, `music_service`, `media_covers`, `preview_service` | `pulse_reels`, `chat_media_uploads`, `pulse_comments` | **R2 + Mux** | `media_worker` |
| **Status** | `/api/pulse/status/*`, `/api/pulse/status/music/search` (42892) | `media_service`, `music_service` | `pulse_status`, `pulse_status_media`, `pulse_status_music`, `pulse_status_live` | R2 | `media_worker` |
| **Messaging** | `/api/pulse/messages/*` (89579–91241), `/api/pulse/communications/*` (90297–90637), `/api/messages/media/init\|upload\|complete\|attach` (91488–91545), `/api/pulse/typing` (89301) | `pulsesoc_communications_engine` (2,330), `messenger_media_foundation` (1,131), `messenger_intelligence_service`, `pulse_chat_bridge`, `chat_health_service`, `command_center_client` | `pulse_messages`, `pulse_conversations`, `chat_media_uploads` | R2 | `media_worker` |
| **Notifications** | `/api/pulse/notifications/*`, `/unread-count` (38343) | `pulsesoc_notification_system` (2,979), `notification_service` (2,306), `push_service` (1,285), `notification_orchestrator`, `notification_health_engine` | `notification_jobs`, `notification_logs`, `notification_delivery_logs`, `notification_preferences`, `notification_schedules`, `notification_failures`, `push_delivery_jobs`, **`push_subscriptions`**, `user_device_tokens`, `pulse_notification_devices`, `expo_push_tickets` | APNs, FCM, **Web Push/VAPID**, Brevo | `email_worker` (mail); push fan-out is in-request |
| **Profile** | `/api/pulse/profile*`, `/api/pulse/users/search` (89881) | `pulse_profile_service`, `profile_viewer_permissions`, `premium_identity_engine`, `user_trust_engine` | `creator_profiles`, `pulse_user_badges`, `pulse_user_privileges`, `profile_audit_logs` | R2 (avatars) | `media_worker` |
| **Social graph** | `POST /api/pulse/follows/toggle` (89538) | `pulse_social_graph_service`, `social_relationship_service`, `reputation_economy_engine`† | `pulse_follows`, `blocked_users` | — | — |
| **Search** | `/api/pulse/search` (41673) + 8 others (§7.1) | `pulse_search_engine` (**29 lines**) | `pulse_posts`, `pulse_comments` (via `LIKE`) | **none** | — |
| **Marketplace** | `/api/pulse/marketplace/*` (54361, 94377, 94498, 94556), cart/offers/returns route packs (`bot.py:1285-1287`) | 27 modules + `services/business_os/` (204 files); `marketplace_reservation_sweeper`, `_settlement_service`, `seller_money`, `seller_lifecycle` | `marketplace_listings`, `marketplace_orders`, `marketplace_sellers`, `marketplace_inventory_reservations`, `marketplace_product_media`, `marketplace_digital_files`, `marketplace_merchant_applications`/`_documents`, `marketplace_reports`, `marketplace_saved_products`, `payout_queue`, `refund_queue`, `chargeback_queue` | **Stripe**, R2, CJ | `pulse_worker` **(orphaned — reservation sweep)**, `supplier_worker` **(orphaned)** |
| **Payments / premium** | `/api/pulse/premium/*`, Stripe webhook, `/pulse/premium/undx` (56546) | `premium_entitlement_service` (1,108), `pulse_payment_router`, `payment_provider`, `stripe_webhook_verification`, `pulse_apple_iap_credits`, `platform_treasury_service`, `pro_access` | `premium_entitlements`, `pulse_ad_wallet_funding_sessions` | **Stripe**, **Apple IAP** | — |
| **Advertising** | `/api/pulse/ads/*` (19426–20208), `/admin/business-os/advertising/*` (24202) | 11 modules (11,213 lines): `pulse_ads_service`, `pulse_ads_os`, `pulse_ad_payments`, `pulse_ads_adsets/_audiences/_insights/_library/_reporting`, `pulse_advertiser_portal`, `ad_policy_engine` | `pulse_ad_campaigns`, `_creatives`, `_impressions`, `_clicks`, `_events`, `_billing_events`, `_invoices`, `_receipts`, `_refunds`, `_placements`, `_frequency_caps`, `_idempotency`, `_moderation_queue`, `_policy_flags`, `_review_board`, `_media_assets`, `_accounts`, `pulse_ad_jobs` | **Stripe**, R2 | **`ads_worker`** (deployed) |
| **Business OS** | `/api/business-os/*` (199 routes), `/admin/business-os/*` | `services/business_os/` (204 files, 65,881 lines) + 4 route packs | `business_os_*`, `business_os_supplier_sync_jobs` | Stripe, CJ | `supplier_worker` **(orphaned)** |
| **UNDX** | `/api/undx/*` (29726–29956), `/api/business-os/undx/*` (26808–27047), `/health/undx` (121057) — 29 routes | 50 services + `services/undx_brain/` + 5 root modules (~56k lines) | `undx_*` run/mission/cost/health tables | **7 AI providers** (OpenAI, Claude, Gemini, DeepSeek, Groq, Meta, Perplexity) | **`undx_worker`** (deployed) |
| **Private Office** | 9 route packs (`bot.py:1335-1380`) | 9 route modules + `services/private_office/` (40 files, 29,584) | `private_office_*` | **Agora** (meetings), R2 (documents) | — |
| **Live** | `/pulse/live/studio/<id>` (49100), `/api/pulse/live/mux/<id>` (49896), `/api/pulse/live/<id>/browser-publish` (51458), `/api/pulse/live/stream` SSE (89360, **flagged off**) | `mux_live_service`, `agora_media_push_service`, `agora_cloud_recording_service`, `live_participants`, `live_distribution_service`, `live_archive_service`, `live_stream_health_service`, `live_discovery_service`, `live_restream_service` | `pulse_live_streams`, `_sessions`, `_viewers`, `_guests`, `_guest_requests`, `_chat`, `_reactions`, `_clips`, `_destinations`, `_restream_targets`, `_scene_presets`, `_moderation`, `_reports`, `_provider_events`, `_webrtc_signals`, `_audio_profiles`, `_audit_logs`, `livestream_access`, `livestream_eligibility` | **Agora → Mux** | `media_worker` (`finalize_live_replay`) |
| **Calls** | Private Office meetings; Agora token mint | `services/private_office/meetings.py`, `live_participants`, `agora_*` | `pulse_live_webrtc_signals`, `private_office_*` | **Agora** | — |
| **Media** | 11 upload routes + the 7-step presign flow (103087–103151) + Mux direct upload (103186, 103281) | `media_service` (1,365), `media_storage`, `media_upload_sessions`, `upload_progress_service`, `media_covers`, `preview_service`, `embed_service` | `chat_media_uploads`, `pulse_media_upload_sessions` | **R2**, **Mux**, ffmpeg | **`media_worker`** (deployed) |
| **Crypto / alerts** | `/api/crypto/*`, `/api/alerts/events` (37669), `/api/live/news` (30098) | `alert_engine` (4,277), `coingecko_client`, `market_intelligence*`, `portfolio_service`, `auto_signals_service`, `market_observations` | `user_alerts`, `user_alert_rules`, `alert_delivery_jobs` | **CoinGecko**, Telegram | **`alert_worker`** (deployed) |
| **Arena** | `/api/arena/*` (120 routes), `/api/arena/realtime/<kind>/<id>` (36576) + `/stream` (36592, **flagged off**) | 19 arena modules + `realtime_service` | `arena_*`, `arena_live_matches`, `arena_play_sessions` | — | — |

\* dead module (§1.2) — the name is a decoy.  † stub under 60 lines (§1.2).

### 8.1 Top shared-backend risks for the web rebuild

1. **The gunicorn config forbids the realtime architecture a web app expects.** 4 sync
   workers × 8 threads, and `bot.py:89367` explicitly disables SSE to protect that pool.
   Web must poll, or `services/command_center_worker/` must be promoted to a deployed
   service with Redis pub/sub. Adding browser tabs to a polling backend multiplies load
   against 32 threads.
2. **R2 bucket CORS is unconfigured and unverifiable from the repo.** Direct-to-R2 upload
   is the only path for anything over 12 MB, and it cannot work from a browser without a
   CORS policy exposing `ETag`. This will be the first thing to break.
3. **Three workers are not deployed** — and one of them (`pulse_worker`) owns both feed
   precompute (`pulse_jobs`) and the marketplace reservation sweep. Building web features
   on either will surface an existing production gap.
4. **Search does not exist.** `LIKE '%term%'` plus 29 lines of Python scoring. Any web
   search UX is a new backend workstream, not an integration.
5. **Agora, not LiveKit** — and the code still says "livekit" in enum values, state
   machines, and telemetry. A web team reading CLAUDE.md or grepping for the transport will
   reach the wrong conclusion and may attempt a LiveKit web client against an Agora
   backend. The real-time audio foundation is hard-protected; do not modify it.

Runners-up: the ffmpeg-missing fallback that sets `thumbnail_url = media_url` (§4.4);
Apple IAP entitlements having no web purchase rail (§7.5); ~60 live-but-vestigial
`*_engine.py` stubs whose names promise capability they do not have (§1.2); and 547
`CREATE TABLE` statements with no migration framework.
