# PulseSoc — Reuse vs Rebuild Matrix

**The rule this document applies:** existing web code may be reused **only** where it is
compatible with the current native architecture, security posture, backend, and database.
Nothing is kept because it exists.

---

## 1. Verdict summary

| Layer | Verdict | Rationale in one line |
|---|---|---|
| **Backend API** (`/api/*`, 1,331 rules) | ✅ **REUSE — wholesale** | Already serves the native app daily; the web just needs callers |
| **Database** (884 tables) | ✅ **REUSE — wholesale** | One platform, one database. A second DB is explicitly forbidden |
| **Service layer** (~707 files, ~272k lines) | ✅ **REUSE** | The domain logic. Untouched by a client rebuild |
| **`services/app_links.py`** | ✅ **REUSE — as-is** | Correct, defensive, already canonical |
| **`templates/_app_link_cta.html`** | ✅ **REUSE — as-is** | The only correct component in the template layer |
| **Design tokens** (native `colors.ts` etc.) | ✅ **REUSE — translate** | Port values exactly to CSS custom properties |
| **Auth endpoints** | 🔶 **REUSE + extend** | Cookie leg exists; needs a decorator and a browser device-id |
| **Jinja templates** (20 files) | 🔶 **PARTIAL** | Shell/layout ideas survive; page bodies do not |
| **Static JS** (`static/*.js`) | 🔶 **PARTIAL** | Salvage specific hardened functions, not files |
| **Service workers** | 🔶 **REBUILD from the better one** | Two diverged copies — see §4 |
| **Inline HTML layer** (~46% of `bot.py`) | ❌ **REBUILD** | 5 competing shells, no escaping, no bundler |
| **`clean_html()` as sanitiser** | ❌ **DELETE** | Not an escaper. 2,217 call sites |
| **Admin console** (296 pages) | ❌ **REBUILD** | Largest + least safe web surface |
| **`/api/arena`** (120 + 37 routes) | ❌ **DELETE** (after audit) | Zero native callers; CoinPilotX-era |
| **`/search`** | ❌ **REBUILD as a new product** | Searches marketing pages, not PulseSoc |

---

## 2. ✅ Reuse wholesale — the platform

This is the strongest finding in the inventory and the reason the "one platform, multiple
clients" architecture is achievable rather than aspirational.

**The backend does not need 236 new endpoints. It needs 236 new callers.** Every endpoint in the
gap already exists, already carries the domain logic, and is already exercised in production by
the iOS app. The web client is a new consumer of a proven API, not a new API.

| Asset | Size | Why it survives untouched |
|---|---|---|
| `/api/*` routes | 1,331 rules | Native-proven |
| `services/` | ~707 files, ~272k lines | All business logic lives here |
| PostgreSQL 18.6 | 884 tables, 9,458 columns | Single shared data plane |
| Stripe / R2 / Mux / Brevo / FCM integrations | — | Client-agnostic |
| `services/app_links.py` | 911 lines | See §3 |

**Corollary for the plan:** the rebuild is a *client* project with a small, well-defined backend
workstream (auth decorator, rate-limit keying, R2 CORS, key split). It is not a backend rewrite.
Anyone scoping it as a backend rewrite has misread the inventory.

---

## 3. ✅ Reuse as-is — the app-promotion layer

`services/app_links.py` and `templates/_app_link_cta.html` are already correct and already
canonical. They are the single best-engineered part of the existing web layer.

**Reuse them unchanged. Do not re-implement.** Specifically:

- `app_store_url()` ignores a `PULSESOC_APP_STORE_URL` env override that is not an
  `apps.apple.com` URL — deliberately, so a bad Railway variable cannot become an open redirect.
- `build_app_link()` raises `AppLinkError` rather than emitting a link whose label promises a
  destination the shipped binary cannot resolve. This is a correctness guarantee, not a nicety.
- `is_web_intent_path()` protects privacy, terms, support, login, checkout and `/dashboard` from
  being converted into app launches.
- The Jinja macros must be imported `with context` or they raise `UndefinedError` at render time.

The **hard constraint** they encode, which the target architecture must respect: the iOS
Associated Domains entitlement claims only `pulsesoc.com`, and the published AASA claims only
`/pulse/*` and `/search*`. **A brand-new path family is silently ignored by every installed copy
of the app until a new iOS build ships.**

---

## 4. 🔶 Partial reuse — salvage functions, not files

### Service workers — two live copies that have diverged

`sw.js` and `service-worker.js` are both registered, at two scopes, and they are **not the same
code**. `sw.js` has `safeNotificationUrl()` hardening at `:186-198` that `service-worker.js`
lacks.

**This is a live security bug, not just duplication.** Which worker handles a given notification
click depends on scope. Verdict: **rebuild one worker, seeded from `sw.js`'s hardened logic, and
unregister the other.** Do not carry two forward.

### Static JS

| Asset | Verdict |
|---|---|
| `safeNotificationUrl()` in `sw.js:186-198` | ✅ Salvage the function |
| `pulsesoc-tokens.css` reduced-motion + contrast blocks | ✅ Salvage — correctly mirrors native `theme.duration()` / `HIGH_CONTRAST_*` |
| `pulse_chat_recovery.js` | ❌ Orphan — delete |
| `static/offline.html` | ❌ Orphan — delete |
| `site.webmanifest` | ❌ Byte-identical duplicate of `manifest.json` — delete one |
| Everything else | ❌ No bundler, no modules — rebuild |

### Jinja templates

20 template files against 496 HTML-producing routes. The templates are the *good* 4% of the web
layer, because a template engine with `autoescape=True` is structurally safe in a way the inline
layer is not. **Reuse the approach, not the markup** — page bodies target a mobile-only layout
that the responsive strategy replaces.

---

## 5. ❌ Rebuild — the inline HTML layer

**The single largest deletion in the project.**

| Metric | Value |
|---|---|
| Share of `bot.py` that is HTML/CSS/JS string literal | **46.7%** (~3.70M chars) |
| HTML-producing routes | 496 |
| Competing shell functions | **5** |
| `pulse_social_shell` document width | one physical line of **11,677 chars** |
| Bundler / framework / module system | **none** |
| Node in the deploy image | **none** — `nixpacks.toml` installs `python311` + `ffmpeg` only |

Design tokens, favicon and i18n are string-spliced into the response body by three
`after_request` hooks.

**Why it cannot be incrementally improved:**

`clean_html()` (`bot.py:119744`) is the de-facto sanitiser — **2,217 call sites** against 4 uses
of `html.escape`. It is a tag stripper, not an escaper. Verified by execution:

| Input | Output |
|---|---|
| `<script>alert(1)</script>` | `alert(1)` — neutralised |
| `<img src=x onerror=alert(1)` | **passes through intact** (no closing `>`, so the regex never matches) |
| `' onmouseover=alert(1) x='` | **passes through intact** |
| `Tom & Jerry` | `&` never encoded |

264 call sites interpolate into an HTML attribute delimiter. **Do not attempt a find-and-replace
across 2,217 sites — that is how escaping bugs get introduced.** The rebuild resolves this class
*structurally*: a template engine with contextual auto-escaping (Jinja `autoescape=True`, or
JSX) makes the default safe. **This is the strongest single argument for not carrying the inline
layer forward.**

Fix `bot.py:28760` independently and immediately — it renders raw database cells into an admin
session with no sanitisation at all, which is the classic privilege-escalation shape. It should
not wait for the rebuild.

---

## 6. ❌ Delete — after a dynamic-call audit

| Asset | Size | Evidence |
|---|---|---|
| `/api/arena` + `/arena` | 157 routes | **Zero native callers.** CoinPilotX-era |
| `/api/pulse/mobile/*` | 30 routes | Duplicate of `/api/mobile/*`; native uses the latter. **Two auth surfaces is security-relevant** |
| `/api/reels` | 12 routes | Second reels surface; native uses `/api/pulse/reels` |
| First `Flask()` assignment | `bot.py:464` | Discarded by the second at `bot.py:1130` |
| Unreachable code | `bot.py:84620-84623` | — |
| 19 dead service modules | — | From the services inventory |
| ~60 vestigial `*_engine.py` stubs | — | Live but empty |
| `.fuse_hidden*` files, `*_REPORT.md` | hundreds | Repo-root housekeeping |

> **Gate on all of §6:** static extraction cannot see a URL assembled at runtime. One week of
> production route-hit logging keyed by client must precede any deletion. The cost of wrongly
> deleting a live payment or auth route is not symmetric with the cost of keeping a dead one.

---

## 7. Decision rule for anything not listed

Ask in order. First **no** decides it.

1. Does the native app depend on it? → if yes, **reuse**, do not touch.
2. Is it below the client layer (API, service, DB)? → **reuse**.
3. Does it render user-controlled data through `clean_html()`? → **rebuild**.
4. Does it have a native caller? → if no, **audit before deleting**.
5. Does it assume mobile-only layout? → **rebuild responsively**.
6. Is it duplicated? → **keep the hardened copy, delete the other** (cf. §4).

---

## 8. What reuse buys

| | Rebuild everything | Reuse per this matrix |
|---|---|---|
| Backend API | rewrite 1,331 routes | **0 new endpoints** |
| Domain logic | re-derive ~272k lines | **0 lines** |
| Database | new schema + migration | **0 changes** |
| App-link layer | re-implement + re-verify AASA | **0 lines** |
| Design language | re-derive from screenshots | translate ~23 tokens |
| Client | build | build |

The rebuild is **one client against a proven platform**. That is the entire thesis of the plan,
and the inventory supports it: the endpoints exist, the data is shared, the logic is shared, and
the only genuinely blocking backend gap is authentication.
