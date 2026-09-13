# PulseSoc Backend — Routes & Auth (web-rebuild readiness)

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

