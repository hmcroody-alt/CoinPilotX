# PulseSoc — Web Rebuild Master Report

**CoinPlotXAI Inc. · pulsesoc.com**
Prepared against the working tree at `main` (`ffd356c5`), 2026-09-12. Production PostgreSQL
18.6 introspected live. **No implementation was performed. No protected system was modified.**

This is the synthesis document. It answers the brief's twenty questions, states the
recommendation, and points at the thirteen supporting deliverables for evidence. Every number
in it was produced by reading code or executing a query against this tree — not from
documentation, not from filenames, and not from the existing `*_REPORT.md` files in the repo
root, which were treated as leads only.

---

## 0. The one-paragraph answer

The native iOS app is the product. The website is not a lagging version of it — it is a
**different product that happens to share a database**. Web and native overlap on **45 API
endpoints out of 1,331**, which is 3.4%. So the rebuild is not a re-skin and not a port: it is
**one new client written against a platform that is already proven in production by the phone**.
That is also the good news, because it means the backend needs **zero new endpoints** — it needs
236 new *callers*. The blocking work is small and specific: authentication for a browser, R2 CORS,
seven duplicate-table decisions, one data sentinel, and four indexes. The genuinely large work is
deleting the inline-HTML layer that constitutes **46.7% of `bot.py`** and cannot be made safe
incrementally.

---

## 1. Recommendation

**Build one client, on the apex origin, against the existing API.**

```
        ┌──────────────┐        ┌──────────────────────────────┐
        │  iOS native  │        │  Web  (pulsesoc.com)         │
        │  Expo 54     │        │  Jinja marketing + SPA/pulse │
        └──────┬───────┘        └──────────────┬───────────────┘
               │                               │
               └──────────────┬────────────────┘
                              ▼
                    Shared HTTP API  (1,331 /api rules)
                              ▼
                    Shared domain logic  (services/, ~272k lines)
                              ▼
                    Shared data  (PostgreSQL 18.6, 884 tables)
                              ▼
                    Shared infrastructure  (Railway, R2, Stripe, Mux, Brevo)
```

Four guarantees make that diagram enforceable rather than decorative. They are stated in full in
`PULSESOC_WEB_TARGET_ARCHITECTURE.md` §0:

| # | Guarantee |
|---|---|
| **G1** | The web client never opens a database connection. It consumes the same JSON the phone consumes. |
| **G2** | New domain logic goes in `services/`, where both clients inherit it. Never in a client. |
| **G3** | The web client ships on the **apex origin** (`pulsesoc.com`), not a subdomain. |
| **G4** | Shape resolution — media IDs, polymorphic entity IDs, the 106-column `users` row — happens at the API layer, never in the client. |

**G3 is the non-obvious one.** The reflexive modern answer is Next.js on `app.pulsesoc.com`. Three
verified facts block it: the deploy image has no Node (`nixpacks.toml` = `python311` + `ffmpeg`);
the iOS Associated Domains entitlement claims only `pulsesoc.com`, so a subdomain is invisible to
universal links; and the app has **zero CORS configuration repo-wide**, which is precisely why
`bot.py:3451` can safely exempt JSON requests from CSRF. Moving to a second origin silently
converts that exemption into a vulnerability.

> **Standing rule this produces:** *any pull request that adds a CORS header to the Flask app must,
> in the same diff, add JSON CSRF enforcement.* The two facts live 3,000 lines apart and one of
> them is load-bearing for the other.

---

## 2. The twenty questions

### Q1 — How large is the native app?

**338,546 lines** of TypeScript/TSX across **1,090 source files** under `mobile-native/src`.
Expo SDK 54, React Native 0.81.5, React 19, TypeScript 5.9, React Navigation, Zustand,
`expo-secure-store`. Bundle id `com.pulsesoc.app`.

### Q2 — How many screens?

| Measure | Count |
|---|---:|
| `*Screen.tsx` files | **147** |
| Distinct registered screen names | **159** |
| Root-stack route entries | 167 |

The three figures differ legitimately: some screens are registered under more than one name, and
a few routes render inline components rather than a `*Screen.tsx` file. 147 is the file count;
**159 is the number the web must map**.

### Q3 — How many navigation destinations?

**182 unique routes** across 185 registrations, in three navigators: auth stack (3), root stack
(167), tabs (15).

**The tab finding matters more than the count.** Fifteen tabs are registered; the custom
`LogiNexusBottomNavigation` renders a **hardcoded five-item array** — Home, Reels, Create,
Messages, Profile. Not role-based, not entitlement-based, not remote-configurable. A module-scope
constant. And because there are **no per-tab stacks**, the tab bar is global chrome: pushing any
of the 167 stack routes replaces the entire viewport including the bar.

**Consequence for web:** the five-item bar is the mobile-breakpoint nav. It is *not* a desktop
information architecture, and the other 10 registered tabs are not hidden features to surface —
they are stack destinations reached from elsewhere.

### Q4 — How many product areas?

**Ten**, catalogued in `PULSESOC_NATIVE_PRODUCT_AREAS.md`: social & content, commerce, business &
growth, intelligence & AI, crypto, account & settings, live/calls/media *(protected — inventory
only)*, admin & moderation, plus navigation and the tab shell in the companion document.

### Q5 — How many components?

**138 `.tsx` files** under `mobile-native/src/components` excluding `__tests__` — 25 at top level,
the rest across 20 feature folders.

The important structural fact: there are only **two** files that function as a cross-app primitive
kit, `LogiNexus.tsx` (7 primitives, all driven by one `tone` prop across 8 tones) and
`Screen.tsx` (layout + state shells). Everything else is feature-local. **The web design system is
therefore a small translation job, not an archaeology project** — see Q-supplement in
`PULSESOC_WEB_DESIGN_SYSTEM_MAP.md` §9, which proposes the CSS custom-property set.

### Q6 — How many API modules?

**109** under `mobile-native/src/api/`. **106 are wired**; 3 are dead (`pulse.ts`, `referral.ts`,
`undxRuns.ts`). They resolve to **330 distinct backend paths**.

A trap for anyone repeating this audit: 31 modules show "no endpoint," which reads as dead. **30 of
the 31 are client-side domain layers with live consumers.** Module-with-no-URL is not evidence of
deadness in this codebase.

### Q7 — How many backend routes?

| Source | Routes | Status |
|---|---:|---|
| `CLAUDE.md` | ~1,538 | **Stale — do not quote** |
| `bot.py` only | 1,777 | Incomplete |
| **Repo-wide, corrected** | **2,146** | **Authoritative** |

**369 routes — 17% of the application — are declared outside `bot.py`**, in Flask Blueprints
registered as optional route packs (162 of them in `pulse_communications_v2/routes.py` alone).
Of the total, **1,331 are distinct `/api` rules** and 727 are non-`/api`.

Two extraction traps produced false results before they were fixed, and any future audit will hit
both: (1) `grep '@app.route' bot.py` misses the 369 blueprint routes, making the private-office and
marketplace-cart families read as non-existent; (2) f-string route constants
(`@cart_blueprint.route(f"{API_PREFIX}/checkout-options")`) mis-record 88 routes unless module-level
constants are resolved — which made four *live* marketplace families read as "native calls an
endpoint that does not exist."

> **Note on superseded figures.** `PULSESOC_EXISTING_WEB_INVENTORY.md` quotes 1,153 `/api` routes
> and a 291/306/79 consumer split from the earlier `bot.py`-only pass. **`PULSESOC_WEB_API_GAP_ANALYSIS.md`
> supersedes it** on every route count. The narrative conclusions in the web inventory are unchanged
> — the overlap got *smaller*, not larger.

### Q8 — How many database tables?

**884 base tables, 9,458 columns, 2,045 indexes — and 0 foreign key constraints.** Verified by
introspection against production Postgres 18.6.

`CLAUDE.md`'s "~170 tables in `AUTO_PK_TABLES`" is wrong by **5×**. `bot.py` carries ~550
`CREATE TABLE` statements, so production also holds a long tail of tables no current code creates.

**There is no migration framework.** Schema is built imperatively in `bot.init_db()`
(`_init_db_impl`, `bot.py:110179–119009`). Every change must be idempotent.

### Q9 — What does the current website actually support?

Honestly: **it reads a post and shows a notification badge.**

The shared 45 endpoints are posts (6), ads (6), notifications (4), camera config (3), status (2),
push (2), crypto (2), account (2), music (2), dashboard/account (2) and 14 singletons. The website
today **cannot** check out a cart, enrol a second factor, upload an avatar, run a seller
application, or open a story.

| Web surface | Count |
|---|---:|
| HTML-producing page routes | 496 |
| …of which admin | 188 |
| …of which static SEO landing pages | 151 |
| …which execute **no data access at all** | **126** |
| `/pulse/*` paths | 172 |
| Jinja template files | 20 |

> **URL coverage is not parity.** `/pulse/marketplace` exists as a page. It calls **none** of the 19
> marketplace endpoints the app uses. The gap is not missing pages — it is pages that do not do
> anything.

### Q10 — What parts of the current website are obsolete?

| Asset | Size | Evidence |
|---|---:|---|
| `/api/arena` + `/arena` | 157 routes | **Zero native callers.** CoinPilotX-era |
| `/api/pulse/mobile/*` | 30 routes | Duplicate of `/api/mobile/*`; native uses the latter. **Two auth surfaces is security-relevant, not just untidy** |
| `/api/reels` | 12 routes | Second reels surface; native uses `/api/pulse/reels` |
| `/search` | — | Term-matches **139 static SEO marketing pages**, and its FAQ still hard-codes CoinPlotXAI seed-phrase questions from the crypto-bot era |
| First `Flask()` assignment | `bot.py:464` | Discarded by the second at `bot.py:1130` |
| 19 dead service modules, ~60 empty `*_engine.py` stubs | — | From the services inventory |
| `pulse_chat_recovery.js`, `static/offline.html`, duplicate `site.webmanifest` | — | Orphans |

> **Gate on all of the above:** static extraction cannot see a URL assembled at runtime. **One week
> of production route-hit logging keyed by client must precede any deletion.** The cost of wrongly
> deleting a live payment or auth route is not symmetric with the cost of keeping a dead one. This
> is workstream **W-B** in the phase plan, and it gates every deletion in the project.

### Q11 — What parts are reusable?

| Layer | Verdict |
|---|---|
| Backend API (1,331 rules) | ✅ **Reuse wholesale** — already serves the phone daily |
| Database (884 tables) | ✅ **Reuse wholesale** — a second DB is explicitly forbidden |
| Service layer (~707 files, ~272k lines) | ✅ **Reuse** — untouched by a client rebuild |
| `services/app_links.py` + `templates/_app_link_cta.html` | ✅ **Reuse as-is** |
| Native design tokens | ✅ **Translate** — ~23 values to CSS custom properties |
| Auth endpoints | 🔶 Reuse + extend |
| Jinja templates (20) | 🔶 Approach survives, page bodies do not |
| Static JS | 🔶 Salvage specific hardened functions, not files |

**The app-promotion layer deserves its own note**, because the brief makes app promotion a standing
requirement. `services/app_links.py` (911 lines) is the single best-engineered part of the existing
web layer and must be reused unchanged:

- `app_store_url()` deliberately ignores a `PULSESOC_APP_STORE_URL` override that is not an
  `apps.apple.com` URL — so a bad Railway variable cannot become an open redirect.
- `build_app_link()` raises `AppLinkError` rather than emitting a link whose label promises a
  destination the shipped binary cannot resolve.
- `is_web_intent_path()` protects privacy, terms, support, login, checkout and `/dashboard` from
  being converted into app launches.

### Q12 — What needs a full rebuild?

**The inline-HTML layer. It is the single largest deletion in the project.**

| Metric | Value |
|---|---|
| Share of `bot.py` that is HTML/CSS/JS string literal | **46.7%** (~3.70M chars) |
| HTML-producing routes | 496 |
| Competing shell functions | **5** |
| `pulse_social_shell` document width | one physical line of **11,677 chars** |
| Bundler / framework / module system | **none** |
| Node in the deploy image | **none** |

**Why it cannot be incrementally improved.** `clean_html()` (`bot.py:119744`) is the de-facto
sanitiser — **2,217 call sites** against **4** uses of `html.escape`. It is a tag stripper, not an
escaper. Verified by execution:

| Input | Output |
|---|---|
| `<script>alert(1)</script>` | `alert(1)` — neutralised |
| `<img src=x onerror=alert(1)` | **passes through intact** (no closing `>`, so the regex never matches) |
| `' onmouseover=alert(1) x='` | **passes through intact** |
| `Tom & Jerry` | `&` never encoded |

264 call sites interpolate into an HTML attribute delimiter. **Do not attempt a find-and-replace
across 2,217 sites — that is how escaping bugs get introduced.** The rebuild resolves this class
*structurally*: contextual auto-escaping (Jinja `autoescape=True`, or JSX) makes the default safe.

Also rebuilt: the **admin console** (296 pages — the largest and least safe web surface, and a
workstream of its own), **one service worker** seeded from the hardened copy, and **`/search`** as
a new product.

### Q13 — What backend and database are shared?

**All of it.** One platform, one API, one database, one service layer, one set of integrations
(Stripe, R2, Mux, Brevo, FCM/APNs/Web Push, Google Cloud Translation, CoinGecko). No second
database. No duplicated business logic, account data, social graph, messages, posts, profiles,
permissions, recommendation logic, media records, or notification state.

What reuse buys, stated plainly:

| | Rebuild everything | Reuse per the matrix |
|---|---|---|
| Backend API | rewrite 1,331 routes | **0 new endpoints** |
| Domain logic | re-derive ~272k lines | **0 lines** |
| Database | new schema + migration | **0 new tables, 0 new databases** |
| App-link layer | re-implement + re-verify AASA | **0 lines** |
| Design language | re-derive from screenshots | translate ~23 tokens |

### Q14 — What web equivalents are missing?

**236 endpoints** that the native app calls and the web has never called. Ranked by family:

| Endpoints | Family |
|---:|---|
| 19 | `/api/pulse/marketplace` — cart, offers, returns, seller listings. **Money path** |
| 17 | `/api/pulse/live` — ⛔ **inventory only, protected** |
| 16 | `/api/pages` — entirely absent from web |
| 14 | `/api/pulse/ads` |
| 13 | `/api/account` — 2FA, recovery codes, trusted devices, session revocation |
| 11 | `/api/pulse/reels` |
| 10 | `/api/business-os/undx` |
| 9 | `/api/mobile/auth` — **the web has 0 of 9** |
| 9 | `/api/pulse/business` |
| 8 | `/api/progress` · 8 `/api/pulse/payments` |
| 7 | `/api/private-office/capital-graph` |
| 6 | `/api/pulse/profile` · 6 `/api/pulse/seller` |
| 5 | `/api/premium` · 5 `/api/pulse/saved` · 5 `/api/pulse/status` |
| ≤4 | dashboard/account, education, messages media, pulse-ai, calls ⛔, translations, follows, friends, groups, orders, rewards, mute, support, portfolio, crypto |

**Three of the 236 are not reachable from a browser as written** — they assume a bearer token in
`expo-secure-store` or return a payload shaped for a native uploader. Those are enumerated in the
architecture doc §4 and §6.

### Q15 — What needs a different UX on web?

Nine areas are **adapted** (same capability, deliberately different interaction) and eight are
**reduced by constraint**. The constraint set has essentially one root cause:

> **Realtime is polling, by deployment topology.** `bot.py:89367`: *"Long-lived browser streams can
> exhaust the main Gunicorn worker pool."* Both SSE routes return `204` with
> `X-Pulse-Realtime-Transport: polling`. No WebSocket server exists. gunicorn runs 4 workers × 8
> threads. **This is an architectural decision, not a backlog item** — messaging, presence, typing
> and read receipts all inherit it.

Adaptations worth naming: reels (vertical full-screen → centred 9:16 with side comments and
mandatory keyboard/pointer nav); create (camera capture → file picker + `getUserMedia`); status
(tap-hold → explicit click/keyboard progress control); biometric unlock → WebAuthn; lock-screen
now-playing → Media Session API (partial).

**And the inverse — seven places where desktop should beat the phone**, because the brief requires
a premium desktop experience rather than a stretched mobile one: the ads campaign wizard
(`AdsCampaignWizardScreen` is 1,985 lines, the largest screen in the app — a wide canvas with
side-by-side targeting, estimate and persistent preview is simply better); Business OS dashboards;
marketplace browsing with a persistent facet panel; two-pane messages; two-pane settings over the
`settings/<id>` registry; Private Office document review; and a third column that keeps a thread
open beside the feed.

**The governing constraint:** *the feed column never exceeds 884px at any breakpoint. Extra width
buys additional columns, never wider rows.*

The constraint is unchanged from the original plan; the figure was corrected in Phase 1c from 680,
which the app turned out not to contain. 884 is the residual of native's own wide canvas
(`1480 − (2×12) − 226 − 314 − (2×16)`), and the breakpoints are 900 and 1480 rather than
768/1120/1600 for the same reason. `scripts/ops/web_token_authority_gate.py` holds the two
stylesheets to one value each.

### Q16 — What technical debt blocks the rebuild?

Ranked by how expensive it is to discover late:

1. **Seven duplicate table lineages.** Messages (`comm_v2_*` = 1,099 rows vs `pulse_messages` = 29),
   notifications (`notifications` = 62,279 vs `pulse_notifications` = 12,895 — **unresolved**),
   status, friends, mutes, sellers/orders, rooms. Building against the wrong family is wasted work
   that surfaces only at integration. **These cost nothing to decide now.**
   > Tell-tale of a dead lineage: `pulse_messages` carries **8 indexes, two of them exact
   > duplicates, for 29 rows.** Index count is a fossil of intent, not of use.
2. **`user_id = 0` in `pulse_posts` — 81% of rows, 1,915 posts.** There is no user with id 0. A feed
   query written the obvious modern way — `INNER JOIN users ON posts.user_id = users.id` — **silently
   drops 81% of the feed.** No error. The current code works only because it resolves authorship in
   Python. Blocks the feed phase and the `pulse_posts.user_id` FK.
3. **Zero foreign keys across 884 tables.** Contained today only because a single backend is the
   only writer. **A second writing client is exactly the condition that ends that containment.**
4. **Type mismatch that SQLite cannot catch.** `marketplace_*` ids are **integer**;
   `business_os_mkt_*` ids are **text**. Local tests run on SQLite, which compares them happily.
   Postgres raises. A web checkout crossing that boundary passes every local test and fails in
   production **on the money path**. Assert on the query binding, not on the behaviour.
5. **`ensure_schema(conn)` can hang a route on Postgres** — passing a route's connection skips the
   commit, the DDL rolls back, and a second connection blocks on the uncommitted catalog lock.
   ~26 unswept call sites. Do not add more.
6. **`OFFSET` pagination with a computed `ORDER BY`.** Browsers deep-paginate far more than phones.
7. **~382 routes registered inside `except Exception` blocks.** One broken feature cannot block
   boot — but a subsystem can silently vanish in production. Check boot logs before assuming a 404
   is a routing bug.

### Q17 — What security risks exist?

Full treatment in `PULSESOC_WEB_SECURITY_MODEL.md`. The five that change the plan:

| # | Risk | Why it matters now |
|---|---|---|
| 1 | **`clean_html()` is not an escaper** (2,217 sites) | The default is unsafe. Structural fix only |
| 2 | **CSP ships with `script-src 'unsafe-inline'`** | CSP exists and is otherwise decent — `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'self'`, `form-action 'self' https://checkout.stripe.com`. `'unsafe-inline'` nullifies it as XSS mitigation, and it is **forced** by the inline-HTML layer. **A bundled SPA can drop it — this is the strongest *new* security argument for the rebuild.** Note CSP is skipped for `/static/`, which is why the SPA `index.html` must be served from a `/pulse/*` route, not as a static file |
| 3 | **The Flask cookie session is not server-revocable** | No `SESSION_TYPE`, no Flask-Session backend — a default client-side signed cookie with no server-side row. **"Sign out everywhere" cannot kill a web session today.** Accept-or-fix decision, owed before launch |
| 4 | **No auth decorator exists** | `grep` for `@login_required` / `@require_login` / `@admin_required` returns **zero** matches. The gate is a copy-pasted three-line preamble repeated **152 times**. Auth is a rebuild, not a port |
| 5 | **`bot.py:28760` renders raw database cells into an admin session with no sanitisation** | Classic privilege-escalation shape. **Fix independently and immediately — it should not wait for the rebuild** |

Plus two duplication bugs that are security-relevant rather than untidy: two live service workers
at two scopes that have **diverged** (`sw.js` has `safeNotificationUrl()` hardening at `:186-198`
that `service-worker.js` lacks), and two parallel auth surfaces (`/api/mobile/auth/*` and
`/api/pulse/mobile/auth/*`).

**And one failure mode nothing else in the inventory had caught:** the AASA route (`bot.py:123670`)
returns **503** when `PULSESOC_APPLE_TEAM_ID` is unset or malformed. Universal links then die
entirely, with **no in-app symptom**. Since the entire app-promotion requirement rests on that
mechanism, it becomes a continuous health check, not a deploy-time check.

### Q18 — What is the safest build order?

Sixteen phases, each gated. Full detail — goal, surface, gate, exit criteria, risk — in
`PULSESOC_WEB_REBUILD_PHASE_PLAN.md`.

| Phase | Content | Gated on |
|---|---|---|
| **0** | Foundations: 4 index changes, R2 CORS `ETag`, route-hit logging, AASA health check | — |
| **1** | Shell, design tokens, marketing + legal pages, app-promotion CTAs | P0 |
| **2** | Auth — decorator, browser device id, session revocability decision | P0 |
| **3** | **Pages** — the deliberate *proof phase* | P2 |
| **4** | Profile + settings | P3 |
| **5** | Feed | **`user_id = 0` decision** |
| **6** | Notifications | **notifications lineage decision** |
| **7** | Media pipeline | R2 CORS |
| **8** | Reels, status, create | P7 |
| **9** | Messages (polling) | comm lineage decision |
| **10** | Search + saved | search **product** decision |
| **11** | Marketplace browse/cart/offers | P4 |
| **12** | **Checkout — the money path** | P11 + binding-level type tests |
| **13** | Business OS, seller, dashboards | P12 |
| **14** | Ads — **web-first, not a port** | P13 |
| **15** | Intelligence layer + GA | all |
| **W-A** | Admin console rebuild — parallel, from Phase 2 | own workstream |
| **W-B** | Route-hit logging — **gates every deletion in the project** | continuous |

**Why Pages is Phase 3.** It is 16 endpoints of plain JSON CRUD: no uploads, no realtime, no native
mechanism, no protected system, and **no web caller today**. It proves the whole architecture —
auth, transport, design system, responsive strategy, test harness — on a surface where nothing can
be quietly half-working because there is no existing implementation to lean on. It is the cleanest
win available and it is deliberately spent on de-risking rather than on visible progress.

**Why not the feed first.** The feed looks like the obvious first phase and is a trap: it is the one
area where the existing web code *partially* works, so partial success is indistinguishable from
success — and it sits behind the `user_id = 0` decision.

### Q19 — What is the complete path to parity?

**Parity is a ledger, not a date.** `PULSESOC_WEB_TEST_AND_PARITY_PLAN.md` holds one row per
capability per phase, every row currently ⬜, and a row turns ✅ only when it passes **against a
live environment**.

General availability requires all three:

1. Every in-scope row in the parity ledger is ✅ against a live environment.
2. All 15 rows of the security launch gate are green.
3. Device QA has passed, **on a real device**, for universal links, Web Push, checkout, and uploads.

Anything still ⬜ is reported as ⬜.

The capability scoring, which is what parity is measured against:

| Verdict | Areas |
|---|---:|
| 🟢 Full parity achievable | 21 |
| 🟡 Adapted — same capability, different UX | 9 |
| 🟠 Reduced by constraint | 8 |
| ⛔ Excluded — protected system | 2 |
| 🚫 Not applicable to web | 3 |

Roughly **70% of the native product can reach full or adapted parity with no backend change**,
because the endpoints exist and the phone exercises them daily. The reduced set is dominated by the
single polling decision; the excluded set is the protected live/calls foundation.

### Q20 — What must explicitly not change?

**Protected systems — inventory only. No web implementation is proposed, scoped, or scheduled for
any of them in any deliverable:**

- Livestream audio and livestream infrastructure
- Audio calls, video calls, call audio routing
- The RTC audio-session foundation
- Pulse Radio's audio foundation

They appear in the gap analysis as a **count only**: 17 native-only `/api/pulse/live` endpoints and
3 `/api/calls` endpoints, plus 21 further `/api/calls` rules consumed by neither client.

Two facts the architecture must respect regardless: **the web has no realtime transport at all**
today — zero `RTCPeerConnection`, zero `new WebSocket`, and the single `EventSource` is gated off at
both ends. And **RTC is Agora, not LiveKit** (`react-native-agora@4.6.2`, `agora-token-builder`,
`AGORA_*` keys, no `LIVEKIT_*` keys). The backend variable names still say `livekit_room` —
`bot.py:49214` renders `<label>Agora channel<code>{livekit_room}</code></label>`. **`CLAUDE.md` is
wrong on this point** and will send a web team hunting for a package that is not in the tree.

Also unchangeable: **no second database**, **no destructive migration**, **no new tables**, and the
native app must not be redesigned around the website.

---

## 3. Decisions owed, with deadlines

Nine decisions are owed by a specific phase. None requires code. All are cheap now and expensive
later.

| # | Decision | Owed before |
|---:|---|---|
| 1 | `user_id = 0` — system user, backfill, or mandated `LEFT JOIN` | **Phase 5 (feed)** |
| 2 | Notifications lineage — `notifications` vs `pulse_notifications` | **Phase 6** |
| 3 | Messages lineage — confirm `comm_v2_*` | Phase 9 |
| 4 | Web session revocability — accept the gap, or add a server-side store | **Phase 2** |
| 5 | Search as a product — Postgres FTS, trigram, or external index | Phase 10 |
| 6 | Groups — live product or not? 45 routes, ~0 callers on either client | before any groups work |
| 7 | Friends / mutes lineage | before either is built |
| 8 | UNDX on web — confirmed **read-only in phase 1**; approval phrases and emergency stop are weaker controls in a browser | Phase 15 |
| 9 | `/open/...` vs `/pulse/...` — **settled: `/open/...` must not be introduced**, because the served AASA claims only `/pulse/*` and `/search*` and a new family is silently ignored by every installed copy of the app until a new iOS build ships | settled |

---

## 4. Schema changes required — the whole list

| Type | Count | When |
|---|---:|---|
| Index additions (`CONCURRENTLY`) | 3 | Phase 0 |
| Index removals (duplicates) | 2 | Phase 0 |
| Data decision (`user_id = 0`) | 1 | Before the feed |
| FK additions on the verified-clean core social graph (`NOT VALID` → `VALIDATE`) | 11 | Incremental |
| Maintenance job — session TTL sweep | 1 | Before launch |
| **New tables** | **0** | — |
| **New databases** | **0** | Forbidden |
| **Destructive migrations** | **0** | Forbidden |

The three index additions are all seq-scan removals on paths the web will hammer:
`lower(users.username)` (every `/@username` profile hit is a sequential scan today),
`lower(users.email)` (login), and `active_sessions.session_hash` (that table has **only** a pkey
index, so session lookup is a seq scan and duplicate hashes are not prevented). All need partial
predicates to tolerate a handful of blank values.

**And one thing that needs no schema change at all:** `user_settings` is a key/value table with
`UNIQUE (user_id, setting_key)`. Web-only preferences are new keys, no DDL.

---

## 5. What this report does not claim

Per the brief's rule against claiming parity without end-to-end tracing:

- **No endpoint was called.** Route existence is not evidence that it works, is authenticated
  correctly, or returns what a client expects.
- **Parity marks are capability judgements from code tracing**, not verified user journeys.
- **Method-level granularity is collapsed** — `GET /x` and `POST /x` count once. Endpoint counts are
  a **floor** on implementation effort, not a ceiling.
- **Inline admin JavaScript is under-sampled**, so `/api/admin` is under-counted on the web side,
  which inflates the "consumed by neither" figure.
- **This report contains no time estimates.** It contains sequence, gates and exit criteria.

**What would raise confidence, cheaply:** one week of production route-hit logging keyed by client.
The native app already sends a distinguishable `User-Agent` and bearer token. That single dataset
would resolve three of the five weaknesses above simultaneously and convert the 872-endpoint
"consumed by neither" inference into a fact. It is Phase 0 work and it gates every deletion.

---

## 6. The deliverables

The fourteen deliverables the brief requires:

| # | Document | Covers |
|---:|---|---|
| 1 | `PULSESOC_NATIVE_COMPLETE_INVENTORY.md` | Navigators + the 5-of-15 tab rule + deep linking (Part I); 10 product areas, all 182 routes, 109 API modules (Part II) |
| 2 | `PULSESOC_EXISTING_WEB_INVENTORY.md` | What the website is today |
| 3 | `PULSESOC_BACKEND_INVENTORY.md` | Routes, auth and authorization (Part I); services, workers, media pipeline, providers (Part II) |
| 4 | `PULSESOC_DATABASE_INVENTORY.md` | 884-table catalog, indexes, constraints |
| 5 | `PULSESOC_NATIVE_TO_WEB_PARITY_MATRIX.md` | Capability parity, native as source of truth |
| 6 | `PULSESOC_REUSE_VS_REBUILD_MATRIX.md` | Keep / extend / rebuild / delete |
| 7 | `PULSESOC_WEB_API_GAP_ANALYSIS.md` | The 236-endpoint gap, method and its weaknesses |
| 8 | `PULSESOC_DATABASE_GAP_ANALYSIS.md` | The 7 lineage decisions, `user_id = 0`, the index work |
| 9 | `PULSESOC_WEB_DESIGN_SYSTEM_MAP.md` | Tokens, components, motion, responsive strategy |
| 10 | `PULSESOC_WEB_TARGET_ARCHITECTURE.md` | The four guarantees, client/auth/data/realtime/URL model |
| 11 | `PULSESOC_WEB_SECURITY_MODEL.md` | Threat model, the 15-row launch gate |
| 12 | `PULSESOC_WEB_REBUILD_PHASE_PLAN.md` | Phases 0–15, workstreams A/B, decision calendar |
| 13 | `PULSESOC_WEB_TEST_AND_PARITY_PLAN.md` | What each test layer can prove; the parity ledger |
| 14 | **This document** | Synthesis and the twenty answers |

Supporting material, retained because the deliverables cite it by name:

| Document | Covers |
|---|---|
| `PULSESOC_NATIVE_NAVIGATION_AND_TABS.md` | Part I of #1, standalone |
| `PULSESOC_NATIVE_PRODUCT_AREAS.md` | Part II of #1, standalone |
| `PULSESOC_BACKEND_ROUTES_AND_AUTH.md` | Part I of #3, standalone |
| `PULSESOC_BACKEND_SERVICES_AND_INFRA.md` | Part II of #3, standalone |
| `PULSESOC_EXISTING_WEB_HTML_SHELL_INVENTORY.md` | The inline-HTML layer in depth |
| `PULSESOC_WEB_SECURITY_FINDINGS_URGENT.md` | The items that should not wait for the rebuild |
| `data/*.json` | Machine-readable route, endpoint and navigation extracts |

---

## 7. Closing

The inventory supports the architecture rather than merely permitting it. The endpoints exist. The
domain logic exists. The data is shared. The session store already treats the browser as a
first-class citizen — **7,446 of its rows are `platform = 'web'` today**. The design language is two
primitive files and ~23 tokens.

What is missing is a client, an authentication path a browser can use, and seven decisions that
cost nothing this week and a great deal in three months.

**Nothing in this report has been implemented. The rebuild has not begun.**
