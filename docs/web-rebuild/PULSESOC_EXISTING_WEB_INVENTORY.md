# PulseSoc — Existing Website Inventory

**Purpose:** an honest, code-backed account of what the *website* is today, before the web
is rebuilt to mirror the native app.

- Working tree: `/Users/hmcherie/Desktop/CoinPilotX`, branch `main` at `ffd356c5`
  (2026-09-12). `bot.py` was **dirty** at measurement time.
- Read-only audit. Nothing in the repo was modified except this file and its sibling
  documents under `docs/web-rebuild/`.
- Every number below comes from a file that was read or a script that was executed against
  this tree. Anything not established that way is marked **UNVERIFIED**.
- Root `*_REPORT.md` files and `PULSESOC_WEB_PRODUCT_INVENTORY.md` were treated as leads
  only, never as evidence.

Companion documents (same directory, produced alongside this one):
`PULSESOC_EXISTING_WEB_HTML_SHELL_INVENTORY.md` (the inline-HTML layer in depth),
`PULSESOC_BACKEND_ROUTES_AND_AUTH.md`, `PULSESOC_BACKEND_SERVICES_AND_INFRA.md`,
`PULSESOC_NATIVE_NAVIGATION_AND_TABS.md`.

---

## Executive summary — the blunt version

**There is no "website" in the sense the rebuild assumes. There is a Flask monolith that
prints HTML, and its largest single product is the admin console.**

Five facts that should frame every decision:

**1. The web and the native app are two different products that share a database, not two
clients of one API.** The web client (inline JS in `bot.py` + `static/js/` + `templates/`)
references **291** distinct `/api/*` paths. The native app references **306**. They share
**79**. That is a **26% overlap**. 227 endpoints native calls have no web caller at all —
including 108 under `/api/pulse`, all 21 `/api/private-office`, all 19 `/api/pages`, all 13
`/api/mobile`. "Mirror the native app on web" is not a re-skin; the web has never called
most of what the app is built on.

**2. The web exercises 21% of the backend.** There are **1,153** registered `/api/*` routes
(1,076 in `bot.py` + 77 in the `pulse_communications_v2` blueprint), **1,139** after
normalising Flask `<converters>`. The web demonstrably calls **242** of them. The other ~900
exist for the app, for admin, for webhooks, or for nothing.

**3. Size comparison is not screens-to-screens.** The native app registers **159 distinct
screen names** across three navigators (root stack 167 route entries, 15 tabs, 3 auth), backed
by **147 `*Screen.tsx` files**. The web has **496 HTML-producing page routes** — but 188 of
them are admin, 151 are static SEO landing pages, and **126 of all page routes execute no
data access whatsoever** (25%). The *consumer* PulseSoc web surface is **~160 routes,
~140 of them HTML**. So: roughly 140 consumer web pages against ~147 native screens — comparable
in count, radically different in content, and only 26% API-compatible.

**4. The front end has no build step, no bundler, no framework, and no module system.**
`nixpacks.toml` installs `python311` and `ffmpeg` and nothing else — there is no Node in the
deploy image. No `webpack.config`, `vite.config`, `rollup.config` or `next.config` exists
anywhere in the repo. The three `package.json` files are all React Native. **46.7% of
`bot.py` by character count — ~3.70M characters — is HTML/CSS/JS string literal.** Cache
correctness depends on a human hand-editing `?v=feed-actions-v2-20260629a` strings inside
Python f-strings. There is nothing to migrate *from*; the rebuild is a rewrite.

**5. The web has no real-time anything.** Zero `RTCPeerConnection`. Zero
`new WebSocket`. One `EventSource`, in `static/js/pulse_realtime.js`, gated off by default at
both ends. Voice and video calls — a core native feature built on Agora — **do not exist on
the web at all**; the only mention of Agora in `static/` or `templates/` is an admin
configuration console. Messaging, presence, typing and notifications are HTTP polling by
deliberate design.

And the detail that will surprise people most: **`/search` does not search PulseSoc.** It
term-matches against 139 static SEO marketing pages. No posts, no users, no listings, no
messages.

---

## 1. Framework and architecture

### 1.1 What it is

| | |
|---|---|
| Server | Flask 3.1.3 monolith, `bot.py` — **123,711 lines / 7,927,279 chars** |
| App object | `webhook_app`, aliased `app` (`bot.py:464`/`473`, and again `1213`/`1222`) |
| Templating | Jinja2 — **20 templates**, 11 distinct ones referenced from `bot.py` |
| Dominant rendering | **inline f-string HTML returned as `Response(...)`** |
| Client JS | 27 hand-written files, no modules, no bundler, no transpile |
| CSS | 21 hand-written stylesheets in `static/css/`, no preprocessor |
| Deploy | Railway + nixpacks (`python311`, `ffmpeg`), gunicorn, 6 processes |

**There is no SPA and no bundler anywhere in the repository.** Verified by searching for
`webpack.config*`, `vite.config*`, `rollup.config*`, `next.config*` and every
`package.json` outside `node_modules` — the only hits are `mobile-native/package.json`,
`mobile/package.json`, `mobile/pulse-react-native/package.json`, all React Native.

### 1.2 Does the legacy `mobile/` app have a dead web export?

**No build output, but a declared intent.** `mobile/app.json:36-38` declares
`"web": {"bundler": "metro"}`, and `mobile/package.json:10` and
`mobile/pulse-react-native/package.json:10` both define a `"web": "expo start --web"`
script; `mobile/pulse-react-native/package.json` depends on `react-dom` 19.2.0 and
`react-native-web` ^0.21.0. **No `web-build/`, `dist/` or `web/` directory exists** under
either `mobile/` or `mobile-native/`. So: someone could run Expo web from the legacy app,
nobody ever shipped it, and no artifact is checked in.

`mobile-native/package.json` also carries `react-dom` ^19.1.0 and `react-native-web` ^0.21.0
(lines 69, 77) — these are Expo SDK defaults. `mobile-native/app.json` has **no `web` key**
at all. **UNVERIFIED:** whether `expo export --platform web` on `mobile-native` would build;
it was not attempted.

### 1.3 The five HTML shells

Nothing in this codebase has one layout. It has five, none of which supersede another.

| shell | location | call sites | routes reached |
|---|---|---|---|
| `pulse_social_shell` | `bot.py:47004-47111` (108 lines) | 97 | 164 |
| `pulse_page_html` | `bot.py:40754-41607` (854 lines) | 3 | 10 |
| `pulse_web_section_shell` | `bot.py:81914-81928` | 10 | |
| `private_office_web_shell` | `bot.py:81931-81941` | 7 | |
| `pulse_section_shell` | `bot.py:44342-44353` | 2 | |

`pulse_social_shell` returns **one single physical line of 11,677 characters**
(`bot.py:47111`) containing the entire document — doctype, 8 stylesheet links, a ~9KB inline
`<style>`, the body, 6 `<script src>` tags and a trailing inline script. It is the
third-longest line in the file.

`pulse_page_html` is the `/pulse` feed and duplicates the same chrome independently:
avatar/name/initials computation (`40794-40799` vs `47009-47013`), the 5-item mobile bottom
nav with the identical chat SVG (`40820-40842` vs `47056-47067`), the same destinations in
a different order. `bot.py:40821` assigns `mobile_bottom_icons` and `40822` immediately
reassigns it — a dead line from a merge.

The comment at `bot.py:47078-47088` records the cost of this in the team's own words: `/pulse`
had a full topbar with search, notifications and a 24-destination rail while 96 other pages
rendered a seven-link row with no search and no notifications — "Two products on one domain,
and the smaller one was what most pages got."

Admin has its own: `admin_page_html` (`bot.py:15632`). SEO has its own path entirely
(`render_seo_landing` → `templates/seo_page.html`, `bot.py:2003-2011`).

### 1.4 `webhook_app = Flask(...)` happens twice

`bot.py:464` and `bot.py:1213` each construct a `Flask` object with identical config, each
followed by `app = webhook_app` (`473`, `1222`). The second wins; the first is garbage.
Verified by scanning lines 474-1212 for any `webhook_app` attachment — **there are none**, so
this is currently harmless. It is a live footgun: any future `@webhook_app.route`,
`before_request`, config, or extension registration placed in that window disappears
silently at boot.

---

## 2. Every user-reachable page

Measured by AST-parsing `bot.py` and classifying each view function body, following up to
three levels of `return other_view(...)` delegation (there are ~90 thin alias views; not
following them mislabels them).

### 2.1 Route census

| | count |
|---|---|
| `webhook_app.route(...)` decorators | **1,777** |
| `/api/*` | 1,117 |
| non-`/api/` (candidate pages) | **660** |
| — of which HTML-producing page routes | **496** |
| — JSON endpoints living outside `/api/` | 101 |
| — redirects only | 35 |
| — file serves (`send_file`/`send_from_directory`) | 12 |
| — non-HTML `Response` (CSV, XML, robots) | 12 |
| — webhooks / unclassified | 4 |

The `/api/` prefix is not a reliable signal: 101 routes outside it return JSON, including 80
under `/admin/*`, 5 `/debug/*`, 5 `/health/*` and 7 `/webhook*`.

### 2.2 By family (non-`/api/`)

| family | routes | HTML pages |
|---|---|---|
| **`/admin/*`** | **281** | **188** |
| `/pulse/*` long tail | 81 | 68 |
| `/dashboard*` | 45 | 44 |
| `/arena*` | 37 | 33 |
| `/pulse/{marketplace,messages,groups,pages,courses}` | 15 | 15 |
| legal / markets / chat / predictions / sports-edge | 15 | 13 |
| checkout / billing / payments / upgrade / verify-email / reset-password | 12 | 12 |
| `/pulse/dashboard` | 12 | 11 |
| webhooks | 11 | 0 |
| `/pulse/{creator,merchant}` | 8 | 8 |
| `/pulse/premium` | 8 | 8 |
| `/pulse/{live,settings}` | 14 | 13 |
| `/education`, `/pulse/camera`, `/pulse/private-office` | 18 | 18 |
| `/pulse/profile` | 5 | 3 |
| `/debug`, `/health` | 10 | 0 |
| 68 single-route prefixes | 68 | 47 |

**43% of the non-API website is `/admin/*`.** 188 admin HTML pages, every one of them an
inline f-string — not one uses a shell or a Jinja template. The admin console is the largest
single web product in this repository, bigger than consumer PulseSoc.

### 2.3 How real are the pages?

Across all 496 HTML page routes:

| | routes | share |
|---|---|---|
| runs a server-side DB query before rendering | 203 | 41% |
| calls a service/engine, no direct DB | 167 | 34% |
| **no data access at all** | **126** | **25%** |
| auth-guarded | 403 | 81% |
| inject client-side `fetch`/`pulseApi` | 212 | 43% |

~75% touch real data. A quarter are navigation furniture. `/pulse/settings`
(`bot.py:80938-80951`) is 13 lines: a title, a sentence, and five links to the pages that do
the work.

### 2.4 The SEO page system

`seo/content.py` (1,123 lines) is a pure data module. Executed live against this tree:

| collection | count |
|---|---|
| `SEO_PAGES` | 39 |
| `MARKET_PAGES` | 21 |
| `COUNTRY_PAGES` | 21 |
| `KEYWORD_CLUSTERS` | 7 |
| `SPORTS_SEO_PAGES` | 7 |
| `ARTICLE_PAGES` | 6 |
| `HUBS` | 3 |
| **`all_public_paths()`** | **151** |
| **`searchable_pages()`** | **139** |

All render through one Jinja template, `templates/seo_page.html`, via `render_seo_landing`
(`bot.py:2003`). Routes at `bot.py:2417-2473`, ending in a **catch-all
`@webhook_app.route("/<slug>")` at `bot.py:2473`** — every unmatched single-segment path
falls into the SEO system. The copy is still CoinPlotXAI crypto-intelligence marketing
(`simple_public_page`, `bot.py:2014-2036`, hard-codes "Is this financial advice?" and
"Does CoinPlotXAI need my seed phrase?" into every page's FAQ), not PulseSoc social.

---

## 3. Routing

### 3.1 Two mechanisms

**Direct decorators.** All 1,777 `@webhook_app.route` decorators are on the single app
object. `register_blueprint` does not appear in `bot.py` at all.

**Route packs.** `_record_route_pack` / `_load_route_pack` (`bot.py:1240-1275`), then **23
`_load_route_pack(...)` calls** at `bot.py:1279-1381`. Each import is wrapped so a failure
logs CRITICAL and boot continues. Status is exposed at `/health/routes` and in
`ROUTE_PACK_STATUS`.

**This is the "silently vanishing subsystem" mechanism.** A 404 in production is as likely to
be a failed import at boot as a routing bug. Check `/health/routes` before debugging
anything.

### 3.2 Where the "missing" templates come from

Eight templates do not appear in any `bot.py` `render_template` call. All eight are
accounted for:

| template | served by | route |
|---|---|---|
| `pulse_messages_v2.html` | `pulse_communications_v2/routes.py:190` | `/pulse/messages-v2` (and `/pulse/messages` when the flag is on, `bot.py:83960`) |
| `pulsesoc_intelligence_center.html` | `pulse_communications_v2/routes.py:286-299` | `/pulse/intelligence`, `/pulse/signals`, `/pulse/alerts`, `/pulse/forecasts`, `/pulse/briefing`, `/pulse/settings/intelligence`, `/pulse/settings/signals`, `/pulse/signals/<key>`, `/pulse/intelligence/<key>` |
| `admin_pulse_ai_learning_center.html` | `pulse_communications_v2/routes.py:256-263` | `/admin/pulse-ai/learning` |
| `admin_galaxy_intelligence_center.html` | `pulse_communications_v2/routes.py:410-418` | `/admin/intelligence` |
| `admin_calls_command_center.html` | `pulse_communications_v2/routes.py:1588`, `1614` | `/admin/calls`, `/admin/calls/test-config` |
| `business_os.html` | `services/business_os_web.py:31-46` | `/business-os` |
| `business_os_commerce.html` | `services/business_os_commerce_routes.py:142-152` | `/business-os/commerce` |
| `app.html` | `bot.py:11679-11682` | `/app`, `/command-center`, `/intelligence`, `/dashboard/intelligence`; also `/chat` (`bot.py:11706`, `chat_mode=True`) |

`app.html` *is* in `bot.py` — it was missed because the call sites sit behind a
`platform_pro_access` Pro gate. The other seven are served from **Flask Blueprints registered
as route packs**, using `.get()`/`.post()`/`.patch()` decorators rather than `.route()`,
which is why a `render_template` grep over `bot.py` never sees them.

`pulse_communications_v2/routes.py` is 67,130 bytes; the blueprint is declared at line 17.

### 3.3 Alias routes

~90 thin views that just call another view. Examples at `bot.py:10981-11060`:
`/pulse/dashboard` → `dashboard_page()`, `/pulse/ai`, `/pulse/creator`, `/pulse/crypto`,
`/pulse/verification[/<track>]`, `/dashboard/home`, `/pulse/compose` →
`redirect("/pulse#create")`. `/pulse` itself carries 8 feed aliases
(`bot.py:41623-41631`). Route count overstates page count by roughly this margin.

---

## 4. Static JavaScript — every file

**27 files, 884,890 bytes**, none minified, none bundled, no module system (no `import`, no
`export` — everything is global scope, load-order dependent).

### `static/js/` — 23 files, 754,646 bytes

| file | bytes | purpose | referenced by |
|---|---|---|---|
| `pulse_messages_v2.js` | 218,913 | the messaging SPA | `templates/pulse_messages_v2.html` |
| `pulse_home_core.js` | 166,349 | feed/home behaviour | `bot.py` |
| `pulse_media_renderer.js` | 82,340 | media rendering/playback | `bot.py`, `templates/pulse_messages_v2.html` |
| `pulse_status_viewer.js` | 52,510 | stories/status viewer | `bot.py` (7 script tags) |
| `pulse_advertiser_portal.js` | 36,372 | ads portal | `templates/pulse_advertiser_portal.html` |
| `pulse_camera_engine.js` | 32,680 | in-browser camera capture | `bot.py` |
| `pulse_messenger_media_viewer.js` | 20,777 | messenger lightbox | `bot.py` |
| `pulsesoc_intelligence_center.js` | 19,925 | intelligence dashboards | `templates/admin_galaxy_intelligence_center.html`, `templates/pulsesoc_intelligence_center.html` |
| `pulse_upload_manager.js` | 15,371 | upload orchestration (Mux) | `bot.py` |
| `pulse_i18n.js` | 14,836 | client i18n | injected globally, `bot.py:2719-2720` |
| `pulse_ads_hooks.js` | 13,694 | ad slots | `bot.py` |
| `pulse_radio.js` | 10,262 | audio player | `bot.py` |
| `pulse_pwa_install.js` | 9,889 | install prompt; registers `/sw.js` at `:207` | `bot.py` |
| `pulseshell_bridge.js` | 9,921 | native-webview bridge | `bot.py` (4 tags) |
| `pulsesoc_promotions.js` | 9,632 | promo surfaces | `bot.py` (5 tags) |
| `admin_ops_center.js` | 8,743 | admin ops console | `bot.py` |
| `pulse_reaction_system.js` | 6,978 | reactions | `bot.py` |
| `pulse_realtime.js` | 5,816 | **the only `EventSource` in the codebase** | `bot.py` |
| `pulse_environment_engine.js` | 5,746 | theme/environment | `bot.py` (3 tags) |
| `pulse_search_bridge.js` | 5,668 | search box wiring | `bot.py` |
| `pulse_chat_recovery.js` | 3,778 | **ORPHAN** | nothing |
| `time.js` | 2,867 | relative timestamps | `bot.py` (6 tags) |
| `pulse_media_picker.js` | 1,579 | file picker | `bot.py` (3 tags) |

### `static/` root — 4 files, 130,244 bytes

| file | bytes | notes |
|---|---|---|
| `notifications.js` | 57,887 | notification center; **registers `/static/service-worker.js` at `:962`** |
| `analytics.js` | 43,736 | loaded by exactly one page — the ads landing at `bot.py:1613` |
| `service-worker.js` | 14,496 | service worker #1 |
| `sw.js` | 14,125 | service worker #2 |

### Orphans and dead weight

- **`pulse_chat_recovery.js` (3,778 bytes) is loaded by no page.** Repo-wide grep finds it
  only in four audit scripts that read it as *text* (`scripts/chat_system_audit.py:26`,
  `scripts/chat_mobile_audit.py:18`, `scripts/chat_realtime_audit.py:23`,
  `scripts/chat_recovery_audit.py:25`) and in one manifest list. Dead.
- `analytics.js` — 43,736 bytes serving a single marketing route.
- **Duplicated primitives.** `pulseApi(url, opts)` is defined *inline inside the Python
  f-string* at `bot.py:47111` and exists in **no** `static/js/` file — so 160+ pages get a
  fetch wrapper that lives only as Python string data, while every `static/js/` module rolls
  its own. `toast()` has **three** independent implementations: `bot.py:47111`,
  `static/js/pulse_home_core.js`, `static/js/pulse_radio.js`.
- **`bot.py` holds 269,368 characters of inline `<script>` JS across 66 script blocks** —
  about a third again as much JS as the entire `static/js/` directory, invisible to every
  JS tool.

Every `<script src>` carries a hand-maintained `?v=` string. There is no content hashing.
Cache correctness is a human discipline.

---

## 5. Styling and design tokens

21 stylesheets in `static/css/`, 780,590 bytes total. The four largest —
`pulse_status_system.css` (130,540), `pulse_desktop_feed.css` (121,270),
`pulse_messages_v2.css` (108,907), `pulse_home_os.css` (89,509) — are **58%** of the CSS.

### The token layer, and what it admits

`static/css/pulsesoc-tokens.css` (14,422 bytes) is the most-referenced stylesheet — **21
references**. Its own header documents its provenance and its failure:

- It was derived **by hand, one-way**, from `mobile-native/src/theme/colors.ts`.
- The audit it records: **23 native semantic tokens vs 159 web custom-property names across
  19 stylesheets; 45 defined with conflicting values; 0 native tokens matched their web
  equivalent.**
- And the structural blocker, in the file's own words: **151 page routes build HTML inline
  inside `bot.py` and cannot be restyled by touching a template.**

So the answer to "is styling shared with the native theme" is: **no.** There was one manual
port, it drifted immediately, and it was never a live dependency in either direction. There
is no token pipeline, no generated artifact, nothing that fails when the two diverge.

### Token injection

`bot.py:2618-2644`, `@webhook_app.after_request pulse_inject_design_tokens`, string-splices
`PULSESOC_TOKENS_LINK` in after `<head>` on every 200 `text/html` response that is not
`/api` or `/static`. Favicon and the global `pulse_i18n.js` tag (`bot.py:2719-2720`) are
injected the same way. **Three layers of HTML are string-rewritten after the view returns.**

Inside `pulse_social_shell` a further ~9KB inline `<style>` re-declares the design system
with its own custom properties, dark `color-scheme`, and four media queries
(`max-width:900px`, `min-width:1024px`, `min-width:1100px`). The site **is** responsive and
does have a real desktop breakpoint.

---

## 6. PWA — the duplication, resolved

| pair | verdict |
|---|---|
| `static/manifest.json` vs `static/site.webmanifest` | **byte-identical** (`diff -q` clean). Only `/manifest.json` is linked from anywhere. `site.webmanifest` is dead weight served at `bot.py:29217`. |
| `static/sw.js` vs `static/service-worker.js` | **NOT identical** — 80 diff lines. Both are live, at different scopes. |
| `static/offline.html` vs `templates/offline.html` | Differ by 4 lines (the static copy is missing 3 stylesheet links). **`static/offline.html` is referenced by nothing** — both service workers fetch `/offline?...` at line 67, which routes to `bot.py:2482` → `render_template("offline.html")`. The static copy is dead. |

### Two service workers, both registered, at two scopes

- `/sw.js` — registered by `templates/account.html:834` and
  `static/js/pulse_pwa_install.js:207`. Served from `bot.py:29222` with
  `Service-Worker-Allowed: /`.
- `/static/service-worker.js` — registered by `static/notifications.js:962`. Scope
  `/static/`.

They have **diverged in security-relevant ways**: `sw.js` has a `safeNotificationUrl(rawUrl)`
hardening function at lines 186-198 that `service-worker.js` **does not have**.
`service-worker.js` in turn has `soundKey` / `badgeAsset` / `notificationTag` refactors that
`sw.js` lacks. Whichever one claims a client, the user gets a different notification
implementation. This is a real bug surface, not cosmetic duplication.

---

## 7. Web authentication vs the native bearer flow

### 7.1 Web

Hand-rolled, no Flask-Login, no Flask-WTF.

Session cookie via Flask's signed `session`. Secret resolution at `bot.py:106/134/136`:
`FLASK_SECRET_KEY` → `SECRET_KEY` → `SESSION_SECRET`, else `secrets.token_hex(32)` with a
loud warning (which would invalidate every session on restart). Cookie config
(`bot.py:465-472`): `HTTPONLY=True`, `SAMESITE="Lax"`, `SECURE` from env,
`PERMANENT_SESSION_LIFETIME=timedelta(days=PERSISTENT_SESSION_DAYS)`,
`SESSION_REFRESH_EACH_REQUEST=True`.

`/login` (`bot.py:6652-6750`, GET+POST) does, in order: `verify_csrf()` → terms gate →
`login_security_preflight` → `check_password_hash` → `session.permanent = True;
session["account_user_id"] = user["user_id"]` → **`issue_mobile_security_tokens(user,
{"source": "web_login", ...})`** → `redirect(safe_redirect_target("pulse_page"))` →
`set_persistent_session_cookie(response, refresh_token)`.

**Web login mints native tokens too.** One credential check produces a session cookie, a
`pulse_refresh_session` persistent cookie (name from `PULSESOC_REFRESH_COOKIE_NAME`,
`bot.py:136`) and a mobile access/refresh token pair. The two auth systems are already
entangled at the login route.

`account_user_id()` (`bot.py:3658-3659`) resolves in a fixed order:

1. `session["account_user_id"]`
2. `account_user_id_from_mobile_access_token()` (bearer, `bot.py:3605`)
3. `restore_account_from_persistent_cookie()`

`require_account()` (`bot.py:5461-5470`) then calls `load_account_by_id`
(`bot.py:4951-4960`), which hits the `users` table **on every request, uncached**.

### 7.2 The gate is a convention, not a decorator

`grep` for `@login_required`, `@require_login`, `@admin_required` in `bot.py` returns
**zero**. The pattern is copy-pasted by hand:

```python
user = require_account()
if not user:
    return redirect(url_for("login_page", next=request.path))
```

`require_account()` appears **152** times; the redirect line **102** times. For the ~174
routes that go through a shell the guard is structural (`bot.py:40755-40757`,
`47005-47007` — the shell *is* the gate). For the rest it is manual. **A new page route that
forgets the preamble and does not use a shell is silently public.**

403 of 496 HTML page routes (81%) are guarded. The unguarded 19% are legal, SEO, landing and
health surfaces.

### 7.3 CSRF

Also hand-rolled (`bot.py:3229-3239`):

```python
def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

def verify_csrf():
    return request.form.get("csrf_token") and request.form.get("csrf_token") == session.get("csrf_token")
```

The code comment immediately below records the team's own audit: **79 form-driven admin POST
endpoints, 42 of which never called `verify_csrf()`, and 39 of the 79 rendered forms never
emitted a `csrf_token` field.** The remediation was two `after_request`/`before_request`
hooks — `inject_admin_form_csrf` (`bot.py:3273`) injects the hidden field into admin HTML on
the way out, `enforce_admin_form_csrf` (`bot.py:3312`) enforces on the way in, with
`CSRF_EXEMPT_ADMIN_PATHS = frozenset({"/admin/login", "/admin/logout"})`.

So CSRF is enforced **by string-rewriting the response body**, for `/admin/*` only. **The
consumer surface has no blanket CSRF enforcement.**

### 7.4 Native

Bearer token + session cookie, refresh via `POST /api/mobile/auth/refresh`, tokens in
`expo-secure-store`. The known interaction problem (the cookie leg short-circuiting the
bearer leg in `account_user_id()`'s `or` chain) is a live issue tracked separately and is out
of scope here.

---

## 8. Which backend endpoints the web actually calls

Method: extract the first string literal from every `fetch(` / `pulseApi(` / `pulseFetch(`
call site in `bot.py`'s inline JS, plus every `/api/...` literal in `static/**/*.js` and
`templates/**/*.html`; normalise `${...}` interpolations and Flask `<converters>` to `*`;
match against registered routes.

| | count |
|---|---|
| distinct `/api/*` literals referenced by the web client | **291** |
| — from inline JS in `bot.py` | 196 |
| — from `static/js/` + `templates/` | 125 |
| exact-matching a registered route | 249 |
| built at runtime, unresolvable statically | 52 |
| registered `/api/*` routes (1,076 `bot.py` + 77 comm-v2; 1,139 after normalising converters) | 1,153 |
| **registered `/api/*` routes the web demonstrably calls** | **242 of 1,139 (21.2%)** |

Client-called families, inline `bot.py` JS: `/api/pulse` 106, `/api/arena` 38,
`/api/dashboard` 7, `/api/crypto` 6, `/api/admin` 5, `/api/quote` 4, `/api/reels` 4,
`/api/education` 3, `/api/premium` 3, `/api/messages` 3, then a long tail of ones and twos.

From `static/js/` + `templates/`: `/api/pulse` 48, `/api/business-os` 31, `/api/admin` 12,
`/api/push` 4, `/api/ai` 4.

### Web vs native overlap

| | count |
|---|---|
| web distinct `/api/*` (normalised) | 291 |
| native distinct `/api/*` (normalised, from `mobile-native/src/api/`) | 306 |
| **shared** | **79** |
| web-only | 212 |
| native-only | 227 |

Native-only families, largest first: `/api/pulse` 108, `/api/private-office` 21,
`/api/pages` 19, `/api/mobile` 13, `/api/account` 13, `/api/business-os` 12,
`/api/progress` 8, `/api/dashboard` 8, `/api/calls` 5.

**This is the single most important measurement in this document.** Bringing the web to
parity with the app means writing web callers for ~227 endpoints that have never had one.

---

## 9. Realtime

**The web has none, by design and by omission.**

Executed against the whole of `static/`, `templates/` and `bot.py`:

| construct | web occurrences |
|---|---|
| `new WebSocket` | **0** |
| `RTCPeerConnection` | **0** |
| `EventSource` | **1** (`static/js/pulse_realtime.js:74-76`) |
| `getUserMedia` | 2 files + 5 in `bot.py` — **local capture only** |
| `AgoraRTC` in `bot.py` | **0** |

The single `EventSource` defaults to `/api/pulse/live/stream` and is gated by
`legacySseEnabled()` and `scopedSseAllowed()` — it only fires if the URL is the
communications-v2 stream. The **server side is gated off too**:
`pulse_communications_v2/routes.py:641` `_main_app_sse_allowed()` reads
`PULSE_MAIN_APP_SSE_ALLOWED`, **default OFF**, and `_polling_fallback_response()` (`:646`)
returns **204 with `X-Pulse-Realtime-Transport: polling`**. `_sse_response` is at `:634`.

**Voice and video calls do not exist on the web.** The native app uses Agora
(`agora-token-builder` server-side). The only occurrence of "Agora" anywhere in `static/` or
`templates/` is `templates/admin_calls_command_center.html` — an admin configuration console
served at `/admin/calls` and `/admin/calls/test-config`
(`pulse_communications_v2/routes.py:1588`, `1614`). `getUserMedia` in
`static/js/pulse_camera_engine.js` and `static/js/pulse_messages_v2.js` is local camera
capture for recording and upload, with no peer connection behind it.

Call-related flags in `pulse_communications_v2/flags.py` all **default false**:
`PULSE_VOICE_NOTES_ENABLED`, `PULSE_AUDIO_CALLS_ENABLED`, `PULSE_VIDEO_CALLS_ENABLED`,
`PULSE_GROUP_CALLS_ENABLED`. Only `PULSE_COMMUNICATIONS_V2_ENABLED` defaults **true**.

Messaging, presence, typing indicators and notification badges are HTTP polling.

---

## 10. Media and CDN

- **Object storage:** Cloudflare R2 via boto3. Public base from
  `os.getenv("R2_PUBLIC_BASE_URL")`, default **`https://cdn.coinpilotx.app`**
  (`bot.py:53482`, `bot.py:104129`) — still the old CoinPilotX domain.
- **Video:** Mux. Direct upload at `bot.py:103186`
  (`/api/pulse/media/mux/direct-upload`), completion at `bot.py:103281`, generic upload at
  `bot.py:103481` (`/api/pulse/media/upload`). `/api/pulse/media/upload` is special-cased in
  a `before_request` at `bot.py:3079`.
- **Client:** `static/js/pulse_upload_manager.js` (15,371 B) orchestrates uploads;
  `static/js/pulse_media_renderer.js` (82,340 B) renders. Mux references appear in
  `pulse_home_core.js`, `pulse_upload_manager.js`, `pulse_status_viewer.js`,
  `pulse_media_renderer.js` and `templates/index.html`.
- **Browser capture:** `static/js/pulse_camera_engine.js` (32,680 B) +
  `static/css/pulse_camera_engine.css`, backing 6 `/pulse/camera/*` routes.
- **Caching:** `/static/` and `/icons/` get `public, max-age=31536000, immutable`
  (`bot.py:2646-2700`) — with no content hashing, which is exactly why the hand-edited `?v=`
  strings exist.

---

## 11. Search, notifications, messaging

### Search — the headline

`/search` (`bot.py:1996-2000`):

```python
def site_search():
    query = (request.args.get("q") or "").strip()[:120]
    results = search_pages(query)
    return render_template("search.html", query=query, results=results)
```

`search_pages(query, limit=12)` (`seo/content.py:1080`) is a **naive term-count scorer over
the static text of the 139 SEO marketing pages**. It does not touch the database.

**The site-wide search box does not search PulseSoc.** It searches the marketing site. There
*is* real in-app search — `/pulse/search` (`bot.py:42041`) backed by `/api/pulse/search`
(`bot.py:41673`), wired by `static/js/pulse_search_bridge.js` — but it is a separate,
narrower surface. Two search systems, and the one on the front door is the wrong one.

### Notifications

`static/notifications.js` (57,887 B) is the notification center and registers
`/static/service-worker.js`. Push spans Firebase/FCM, APNs and Web Push. Delivery to an open
page is polling (§9). Two service workers means two notification click-handling code paths
with divergent URL hardening (§6).

### Messaging — the one surface already migrated

`/pulse/messages` (`bot.py:83960-83983`): when `pulse_communications_v2` flags are enabled
(**default true**) and there is no `?legacy=1`, it returns
`render_template("pulse_messages_v2.html")` — a real Jinja template plus a 218,913-byte
client SPA. Otherwise it falls back to the inline `pulse_communications_page("")`.
`/pulse/messages/<int:conversation_id>` (`bot.py:84598`) follows the same pattern and has
**unreachable dead code at `bot.py:84620-84623`, after the return**.

The entire block is wrapped in `try/except Exception` (`83962`/`83981`) that logs and
silently falls back to legacy. A template error in v2 does not surface as an error — it
surfaces as the old UI.

**Messaging is the proof the migration is possible and the template for how to do it.**
It is also the only surface that has been done.

---

## 12. Admin surfaces

**281 routes, 188 HTML pages, 80 JSON endpoints, all under `/admin/*`. Not one uses a shell
or a Jinja template — every page is an inline f-string** built by `admin_page_html(title,
body, admin=None)` (`bot.py:15632`).

- Auth: `require_admin_page(permission)` (`bot.py:18364`), login at `bot.py:15987`.
- `/admin/users` at `bot.py:13334`.
- Blueprint-served admin: `/admin/pulse-ai/learning`, `/admin/intelligence`, `/admin/calls`,
  `/admin/calls/test-config`, `/admin/health/deep`,
  `/admin/health/messenger-idempotency` (all in `pulse_communications_v2/routes.py`).
- Cache: all `/admin*` responses get `no-store` (`bot.py:2646-2700`).
- CSRF: enforced by response-body rewriting, `/admin/*` only (§7.3).

Two findings worth flagging for the rebuild:

**`bot.py:28760`**, in `admin_analytics_page` (28754-28819), interpolates raw database cells
into HTML with **no escaping at all** — not even the (inadequate) `clean_html`:

```python
body = "".join("<tr>" + "".join(f"<td>{str(cell)[:180]}</td>" for cell in row) + "</tr>" for row in rows)
```

The cells include end-user-originated email/SMS opt-in values. Audience: an administrator.

**`bot.py:28779`** puts `request.args.get('password','')` straight into a double-quoted
`href`. It sits behind `require_admin_password()` so it is not exploitable unauthenticated —
but the design flaw is that **the admin password travels in the query string**, where it
lands in proxy logs, browser history and `Referer` headers. This is the only direct
`request.args`-into-HTML interpolation in all 123,711 lines.

---

## 13. Deployment, environment, caching

### Deploy

`nixpacks.toml` — the entire file:

```toml
[phases.setup]
nixPkgs = ["python311", "ffmpeg"]
```

**No Node. No build step. No asset pipeline.** Static files are served exactly as committed.

`Procfile` — **6 processes**, not the 3 that older docs claim:

```
web: gunicorn bot:app --bind 0.0.0.0:${PORT:-8080} --workers ${WEB_CONCURRENCY:-4} --threads ${WEB_THREADS:-8} --timeout 120
undx_worker, email_worker, ads_worker, alert_worker, media_worker
```

(`pulse_worker.py` and `telegram_worker.py` exist in the repo and are **not** in the
Procfile.)

### Data

SQLAlchemy over SQLite locally (`coinpilotx.db`), PostgreSQL via `DATABASE_URL` in
production. `services/db.py` is the accessor. Schema is created imperatively in
`bot.init_db()` — no migration framework.

### Headers and caching — `bot.py:2646-2700`, `add_pwa_headers`

| path | Cache-Control |
|---|---|
| `/static/`, `/icons/` | `public, max-age=31536000, immutable` |
| `/pulse*`, `/admin*` | `no-store` |
| sitemaps, manifests | `public, max-age=300` |
| `/sw.js`, `/static/service-worker.js` | `no-store` + `Service-Worker-Allowed: /` |

Also set here: security headers, Permissions-Policy, and a CSP with
`script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com`. **`unsafe-inline`
is mandatory given §1 — the entire site is inline script and inline style. CSP provides no
XSS protection here and cannot until the inline-HTML layer is gone.**

### Feature flags that change what the website *is*

| flag | default | effect |
|---|---|---|
| `PULSE_COMMUNICATIONS_V2_ENABLED` | **true** | `/pulse/messages` serves the Jinja SPA vs the inline legacy page |
| `PULSE_MAIN_APP_SSE_ALLOWED` | **off** | SSE vs polling |
| `PULSE_{VOICE_NOTES,AUDIO_CALLS,VIDEO_CALLS,GROUP_CALLS}_ENABLED` | **false** | call features |
| `BUSINESS_OS_*` | **off** | every Business OS commerce controller 404s |

`services/business_os_commerce_routes.py` adds 37 API routes via `gw.ROUTES` +
`add_url_rule`, and **every controller is dark (404) until its flag is on**.

Other integrations wired in: Stripe, Telegram, Mux, Brevo, Cloudflare R2, Firebase/FCM,
APNs, Web Push, Google Cloud Translation, CoinGecko, optional Redis. `.env.example`
documents ~180 keys.

Miscellaneous served routes at `bot.py:29086-29260`: `/robots.txt`, `/sitemap*.xml`,
`/llms.txt`, `/manifest.json` (29210), `/site.webmanifest` (29217), `/sw.js` (29222),
`/icons/...`, `/favicon`, `/indexnow-key.txt`.

---

## 14. Dead, duplicated and obsolete — stated directly

**Dead:**

- `static/js/pulse_chat_recovery.js` (3,778 B) — loaded by no page.
- `static/offline.html` — referenced by nothing; both service workers fetch `/offline`.
- `static/site.webmanifest` — byte-identical to `manifest.json`, linked from nowhere, still
  routed at `bot.py:29217`.
- `bot.py:84620-84623` — unreachable code after a `return`.
- `bot.py:40821` — assignment immediately overwritten by `40822`.
- The first `webhook_app = Flask(...)` at `bot.py:464-473` — constructed and discarded.
- `mobile/` legacy app's declared Expo-web target — scripts and deps present, never built,
  no artifact.

**Duplicated:**

- **Two service workers, both live, diverged in security-relevant ways** (§6). This is the
  most dangerous duplication in the repo.
- **Five HTML shells**, two of which (`pulse_social_shell`, `pulse_page_html`) independently
  reimplement the same chrome and have measurably drifted.
- **`toast()` × 3.** **`pulseApi()` defined only inside a Python string.**
- **Two search systems**, the wrong one on the front door.
- **Two manifest files**, identical.
- **Two auth token systems** minted by one login route.

**Obsolete:**

- The SEO layer's copy is CoinPlotXAI crypto-intelligence marketing — 151 public paths whose
  FAQ hard-codes seed-phrase and financial-advice questions on a social platform.
- Default CDN host is `https://cdn.coinpilotx.app`.
- `/pulse/premium` (`bot.py:56361-56484`) sells features that are not shipped: "Founder
  checkout is being connected" (`56392`), benefits "prepared in backend entitlements"
  (`56398`), "tracked backend-side for future routing" (`56400`).
- 126 page routes with no data access — static link hubs, e.g. `/pulse/settings`.

**Structural debt the rebuild must not inherit:**

`clean_html` (`bot.py:119744-119747`) is a **tag stripper, not an HTML escaper**:

```python
def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text
```

It does not escape `<`, `>`, `&`, `"` or `'`. Executed: `x' onmouseover='alert(1)` passes
through unchanged; so does `<img src=x onerror=alert(1)` (no closing `>`, so the regex does
not match, and the next `>` in the surrounding template closes the tag). It is called
**2,217 times**. `html.escape` is imported at `bot.py:20` and used **4 times**. Of the 2,217
sites, **264 interpolate into an HTML attribute delimiter** (210 single-quoted, 54
double-quoted) and 67 of those reference user-controllable fields.

It cannot be fixed in place — 2,217 call sites depend on the stripping behaviour. The
rebuild's job is to separate the two concerns: **strip on write, escape on render**, with an
autoescaping template engine. The 264 attribute-context sites are the migration checklist.

---

## Unverified / out of scope

- **UNVERIFIED:** whether all 496 HTML routes actually register at boot in production.
  Route packs fail soft (§3.1). Not checked against a running instance; `/health/routes`
  would answer it.
- **UNVERIFIED:** production values of every flag in §13. Defaults are from source.
- **UNVERIFIED:** runtime behaviour of any page. This is a static read; nothing was executed
  against a server and no payload was fired at a live instance.
- **UNVERIFIED:** whether `expo export --platform web` would succeed on `mobile-native`.
  Not attempted.
- **UNVERIFIED:** the 52 client `/api` literals built at runtime (§8) — the 21.2% coverage
  figure is a floor, not a ceiling.
- All measurements are against a **dirty `bot.py`**. Re-run before using any number here in
  a contract or estimate.
