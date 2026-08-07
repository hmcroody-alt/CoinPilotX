# PulseSoc Web Parity — Phase 2: Canonical Route Manifest

**Generated:** 2026-08-05
**Source snapshot:** `bot.py` @ md5 `522b9419c4283966d27d99abd5208720`, copied 02:19:57Z
**Repo HEAD:** `5f76e30d0f52b634b2f7950754027f62b7947d49` (`codex/emergency-live-audio-recovery`)
**Method:** Python `ast` parse. Read-only. No tracked file modified.

> Snapshot note: `bot.py` is being edited concurrently by other agents (mtime 02:08:04Z).
> All figures below are pinned to the md5 above so they are reproducible.

## Artifacts

| File | Contents |
|---|---|
| `route_table.json` / `.csv` | All 1,539 routes: path, methods, handler, line, product area, surface, auth, render strategy, alias grouping |
| `auth_audit.json` | Per-handler authorization enforcement analysis |
| `native_map.json` | 89 native screens → API modules → backend endpoints |
| `parity_matrix.json` / `.csv` | Per-product-area backend/native/web counts + classification |
| `scripts/web_parity/*.py` | The three extractors, re-runnable |

---

## 1. Route census

| Metric | Count |
|---|---|
| Route decorators | 1,539 |
| Unique paths | 1,493 |
| Distinct handler functions | 1,357 |
| — API (`/api/*`) | 952 |
| — Admin (`/admin*`) | 257 |
| — User-facing pages | 330 |
| **Alias routes (multiple paths → one handler)** | **182** |

### Render strategy — the defining structural fact

| Strategy | All routes | Page routes only |
|---|---|---|
| `json_api` (jsonify) | 708 | 16 |
| **`inline_html`** | **599** | **151** |
| `other` | 125 | 61 |
| `redirect` | 87 | 82 |
| **`jinja_template`** | **20** | **20** |

**599 routes build HTML by string concatenation inside `bot.py`. Only 20 use a
template.** Handlers interpolate f-strings directly — e.g. `dashboard_ads_page`
(line 9498) emits `<article class="ads-command-card">…</article>` inline with
hardcoded class names and metric markup.

This is the root cause of the drift the mission describes, and it reframes
Phases 3–4. There is no view layer, no component boundary, and no token layer to
align against. "Make the website match native" is not a restyle — it requires
introducing a view layer that does not exist, then migrating 151 inline-HTML
page routes onto it. **Every downstream surface phase depends on this.**

---

## 2. Authorization model

Initial automated pass reported "330/330 page routes have no auth decorator."
**That figure is misleading and was corrected by manual verification** — auth is
enforced imperatively in the function body, not declaratively.

Three distinct enforcement patterns found:

1. `user = require_account()` then `if not user: return redirect(...)` — 113 handlers
2. `user, state = _<area>_state_for_current_user()` then `if not user: redirect` — e.g. `dashboard_ads_page`, `dashboard_creator_page`
3. Delegation to another guarded page function — e.g. `dashboard_account_security_page` → `pulse_security_settings_page`

| Result | Count |
|---|---|
| Page handlers | 253 |
| Auth enforced (any of the 3 patterns, ≤3 call levels deep) | 171 |
| No auth detected | 82 |
| — of those, conventionally public (legal, help, login, assets, health) | 56 |
| — flagged for review | 26 |
| **— confirmed genuine exposure after manual inspection** | **0** |

The two that looked user-private were manually read and are pure redirects:
`/pulse/bookmarks` → `redirect("/pulse/saved")`, and `/pulse/live/<live_id>` →
`redirect(pulse_live_watch_url(live_id))`. Neither touches data.

**Finding: this is an auditability problem, not a vulnerability.** Zero page
routes use a declarative guard, so no static tool — and no reviewer — can prove
which routes are protected without following call chains by hand. Phase 5 should
introduce a decorator (or `before_request` map) so authorization becomes
assertable, and add a test that fails when a page handler has no declared
policy.

---

## 3. Parity matrix

Backend = routes registered under `/api/<area>`. Native = distinct endpoints
referenced from `mobile-native/src`. Web = user-facing page routes.

| Product area | Backend API | Native uses | Web pages | Classification |
|---|---:|---:|---:|---|
| pulse | 324 | 117 | 120 | PARTIAL |
| business-os | 199 | 10 | **0** | BACKEND+NATIVE_ONLY |
| arena | 120 | 0 | 38 | WEB_ONLY |
| marketplace | 0 | 0 | 2 | WEB_ONLY |
| messages | 17 | 4 | 10 | PARTIAL |
| reels | 12 | 0 | **0** | BACKEND_ONLY |
| live | 6 | 0 | 11 | WEB_ONLY |
| undx | 8 | 0 | 2 | WEB_ONLY |
| account | 15 | 14 | 4 | PARTIAL |
| payments | 8 | 0 | 2 | WEB_ONLY |
| alerts | 9 | 0 | 1 | WEB_ONLY |
| dashboard | 29 | 10 | 40 | PARTIAL |
| admin | 37 | 0 | **0** | BACKEND_ONLY |
| mobile | 14 | 10 | 0 | BACKEND+NATIVE_ONLY |
| push | 8 | 2 | 0 | BACKEND+NATIVE_ONLY |
| crypto | 19 | 1 | 2 | PARTIAL |

Counting caveat: "native uses" counts endpoints reachable from a screen's
imported API modules; screens sharing a module inherit its endpoints. Arena/reels
showing 0 native usage means those areas are consumed through generic feed
modules, not that they are absent from the app. Treat the zeros in the *web*
column as the load-bearing signal.

### Largest structural gaps

**Business OS — 199 backend routes, 0 web pages.** The single biggest gap in the
platform. Native has `BusinessOsScreen`, `BusinessHubScreen`,
`BusinessOsAdvertisingScreen`, `BusinessOsInsightsScreen`,
`BusinessOsPaymentsScreen`, `BusinessProfileScreen`, plus 49 `/admin/business-os`
routes. The web expression is missing entirely.

**Marketplace — 0 `/api/marketplace` routes, 2 web pages.** Marketplace is
namespaced under `/api/pulse/marketplace/*`, so it isn't a missing backend — but
web coverage is 2 pages (`/pulse/marketplace`, `/pulse/marketplace/create`)
against 9 native commerce screens (`MarketplaceScreen`,
`MarketplaceManagerScreen`, `SellerStoreScreen`, `SellerApplicationScreen`,
`SellerListingComposerScreen`, `StoreDashboardScreen`, `OrdersManagerScreen`,
`BuyerOrdersScreen`, `CommerceInboxScreen`). No web orders, offers,
fulfillment, returns, or disputes surface.

**Reels — 12 backend routes, 0 web pages.** Native `ReelsScreen` exists; web has
no reels destination at all.

---

## 4. HIGH PRIORITY — native calls endpoints that do not exist in the backend

19 of 187 native-referenced endpoints have no matching route. Excluding
template-literal artifacts (`${suffix}`, `${listingId}`), these are unresolved:

```
/api/calls/active
/api/calls/start
/api/calls/voip-token
/api/calls/voip-token/revoke
/api/pulse/communications/v2
/api/pulse/communications/v2/conversations
/api/pulse-ai/actions/cancel
/api/pulse-ai/actions/confirm
/api/pulse-ai/message
/api/pulse/mobile/settings/data-export
```

Corroborating evidence — `bot.py` line 12095 contains:

```python
is_call_request = request.path.startswith(("/api/calls/", "/api/pulse/communications/v2/"))
```

Middleware explicitly anticipates these paths, yet **no handler is registered for
them.** Verified absent by three independent checks: zero `@route` decorators
matching, **zero `add_url_rule` calls** anywhere in `bot.py`, and **zero
`register_blueprint` calls**. So they are not dynamically registered either.

Interpretation — either the native app is calling endpoints that 404 against
this backend, or the routes live in a module not present at this SHA. Given the
branch is `codex/emergency-live-audio-recovery` and the repo has an open Live
audio incident, **missing `/api/calls/*` is a plausible contributor and should be
triaged before any website work.** This finding is about backend/native parity,
not web, but it surfaced from the same inventory and is more urgent than
anything on the web side.

---

## 5. Route hygiene — Phase 18 targets

- **182 alias routes.** Confirmed dead group: `/pulse/home`, `/pulse/legacy-home`,
  `/pulse/home-legacy`, `/pulse/old-home`, `/pulse/legacy` — five paths, one
  handler, all redirecting to `/pulse`.
- Further duplicates: `/pulse/messages-legacy`, `/arena-preview`,
  `/admin-dashboard` vs `/admin`.
- **82 page routes are pure redirects** — a quarter of the web surface is
  forwarding, not rendering.

---

## 6. What is NOT established

Stated plainly, because the mission forbids unevidenced parity claims:

- Railway deployed SHA, website deployed SHA, native build SHA — **unverified**, no access from this environment
- No live browser QA performed
- No visual/design comparison performed
- Native "uses" counts are import-graph derived, not runtime traced
- Product areas below were counted but **not individually inspected**: Arena (157 routes), Operations/Admin (293 routes)

---

## 7. Recommended order

1. **Triage the missing `/api/calls/*` and `/api/pulse-ai/*` routes.** Highest urgency, possibly implicated in the open Live incident.
2. **Declarative authorization** (Phase 5 prerequisite) — make auth assertable before changing 330 page routes.
3. **Web view layer + design tokens** (Phases 3–4) — the gate on every surface phase. Nothing else should start first.
4. **Business OS web surface** (Phase 12) — 199 backend routes with zero web expression; largest single parity gap.
5. **Marketplace** (Phase 10), then **Reels** (Phase 7).
6. Route hygiene (Phase 18) can run in parallel; it is low-risk and independent.

---

## 8. 2026-08-06 milestone — design-token layer wired (Phases 3–4, first slice)

Evidence-based statement of what changed, nothing more:

**Done**
- `static/css/pulsesoc-tokens.css` (the canonical native-mirroring token file) was
  previously referenced by **zero** templates or routes — an orphaned file. It is now
  loaded (before page CSS, so page palettes can consume it) by:
  `templates/pulse_messages_v2.html`, `templates/pulse_advertiser_portal.html`,
  `templates/pulsesoc_intelligence_center.html`,
  `templates/admin_galaxy_intelligence_center.html`, and the admin ops-center inline
  shell in `bot.py` (line ~14477).
- 37 `:root` palette entries across 5 legacy CSS files now derive from canonical
  tokens with their original hex as fallback (`var(--pulse-palette-*, <old-hex>)`):
  `admin_ops_center.css` (9), `pulsesoc_intelligence_center.css` (8),
  `pulse_advertiser_portal.css` (8), `pulse_messages_v2.css` (8),
  `pulse_messenger_media_viewer.css` (4). Zero visual regression risk where the
  token file is absent (fallback = old value); token-driven where it is present.

**Not done (explicitly)**
- Hundreds of scattered hex literals deep in `pulse_messages_v2.css` (gradients,
  per-theme `--control-accent` variants, on-accent text colors) are intentionally
  untouched — they are theme variants without canonical token equivalents.
- No other Phase 3–29 work: no view layer for the 599 inline-HTML routes, no
  Business OS / Reels web surfaces, no browser QA. Section 6 above still stands.
