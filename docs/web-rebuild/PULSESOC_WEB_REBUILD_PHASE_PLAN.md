# PulseSoc — Web Rebuild Phase Plan

**Scope:** Stage 13. Phases 0–15, plus two workstreams that deliberately do **not** fit the
phase model.

**The ordering principle:** each phase must be shippable on its own, and the order is chosen so
that **the riskiest assumption in the project is tested as early as it can be, on the cheapest
possible surface.** That is why Pages — a feature nobody would prioritise on product grounds —
is Phase 3.

---

## 0. How to read this plan

Every phase carries the same five fields.

| Field | Meaning |
|---|---|
| **Goal** | What a user can do at the end that they could not at the start |
| **Surface** | The endpoints and screens involved |
| **Gated on** | What must be *decided* or *fixed* first. A phase cannot start with an open gate |
| **Exit** | Objective, checkable. Not "feels done" |
| **Risk** | The specific way this phase fails |

**No durations.** The inventory supports ordering and dependency claims; it does not support
estimates, and inventing them would be the kind of guess this project's rules forbid.

**Two rules that apply to every phase:**

1. **No endpoint is claimed as working until it has been called against a live environment.**
   Route existence is not proof of behaviour, auth or response shape. Every 🟢 in the parity
   matrix is a hypothesis until the corresponding row in
   `PULSESOC_WEB_TEST_AND_PARITY_PLAN.md` passes.
2. **Every phase ships the App Store CTA** on its public-facing surfaces, via
   `services/app_links.py` / `templates/_app_link_cta.html`. Never a hand-written URL.

---

## 1. The two things that are not phases

### W-A — Admin console (parallel workstream, starts alongside Phase 2)

**296 page routes + 90 API routes.** The admin console is **larger than the consumer product it
administers** (173 `/pulse` routes). Any plan that schedules it as a final cleanup phase has
mis-scoped roughly a third of the work — so it is not scheduled as one.

It is also the surface with the worst XSS exposure *and* the strongest authorization
(330/330 gated, audited, fails closed). Those two facts belong in the same sentence.

- `bot.py:28760` — raw database cells into an admin session, no sanitisation — is fixed in
  **Phase 0**, not here. It does not wait.
- The 9 routes behind `require_admin_password()` (query-string shared secret, `==` comparison,
  no attribution) are **never exposed to the web rebuild**.

### W-B — Route-hit logging and legacy decommission

**Starts in Phase 0 and gates every deletion in the project.**

Static extraction cannot see a URL assembled at runtime. 872 `/api` routes have no statically
resolvable caller in either client — that is an inference, not a deletion list. One week of
production route-hit logging keyed by client must precede any deletion of `/api/arena` (157
routes), `/api/pulse/mobile` (30), `/api/reels` (12), the 19 dead service modules, or anything
else.

> **The cost of wrongly deleting a live payment or auth route is not symmetric with the cost of
> keeping a dead one.** Log first. Deletions land continuously as evidence accumulates, never as
> a big-bang phase.

---

## 2. Phase 0 — Foundations and unblockers

**Goal:** remove every blocker that would otherwise be discovered mid-phase, when it is
expensive.

**Surface:** no user-visible change.

| Workstream | Items |
|---|---|
| **Auth** | Fix `account_user_id()` to verify both credentials and deny on mismatch. Ship the auth decorator + **boot-time default-deny assertion**. Unify CSRF on one header contract. Split session/bearer signing keys |
| **Infra** | ~~Verify R2 CORS against the **live bucket**~~ **done 2026-09-13 — confirmed absent** (`scripts/ops/r2_cors_probe.py`); **applying the policy is still owed and is a human action**, see the risk note below. ~~Shared rate-limit store (Redis) + distributed mode on~~ **done** (5920d542). ~~`GET /health/routes` as a hard deploy gate~~ **done** (4a64915f: build-time contract gate + deploy-time liveness gate), still to extend with a feature-flag snapshot. AASA health check returns 200 with non-empty `details[]` |
| **Database** | 3 index additions (`lower(users.username)`, `lower(users.email)`, `active_sessions.session_hash`) — all `CONCURRENTLY`, all with a partial predicate for blank values. 2 duplicate-index removals. Session TTL sweep |
| **Security** | **Fix `bot.py:28760`** |
| **Client** | Repo scaffold, CI build producing hashed static assets, no Node in the deploy image |
| **W-B** | Route-hit logging live, keyed by client |

**Gated on:** nothing. This is the entry point.

**Exit:**
- A route registered with no auth decorator and not on the public allowlist **fails boot**.
- A browser multipart upload to R2 completes end to end in a scratch harness (proves `ETag`).
- `/@username` is an index scan, verified by `EXPLAIN` on production.
- Rate limits are global, not per-worker, verified across 4 workers.
- `bot.py:28760` renders escaped output.

**Risk:** the R2 CORS item is the one that silently blocks four later phases at once (avatar,
cover, reels, seller documents). It is first in the list for that reason. **Never run DDL
against production to test it** — verify on a throwaway Docker Postgres 18; SQLite reproduces
neither the type nor the constraint behaviour.

> **OWED, and owed to a person rather than to a commit — R2 bucket CORS.**
> Measured 2026-09-13: `pulse-media2` has **no CORS configuration at all**. A browser
> preflight returns 403 and R2 answers `CORS not configured for this bucket`, so every
> browser upload fails at preflight — not only the multipart ones this plan worried about.
>
> The probe is read-only by deliberate choice and **does not apply the fix**. Bucket CORS is
> production infrastructure and the blast radius of a wrong `AllowedOrigins` is every origin
> on the internet holding an intercepted presigned URL, so naming the real origins needs a
> human. `python3 scripts/ops/r2_cors_probe.py` prints the exact policy and the
> `aws s3api put-bucket-cors` command to apply it.
>
> This is written here, rather than only in the inventory, because a confirmed blocker whose
> remediation is *intentionally* not automated is the kind that gets marked "investigated"
> and then forgotten. Phase 0 does not exit and **Phase 7 cannot start** until a re-run of
> the probe exits 0 — it reads behaviour rather than configuration, so a clean re-run is
> proof the change actually took, not proof that someone edited a config page.

---

## 3. Phase 1 — Shell, design system, marketing surface

**Goal:** the site renders in the real design language at every breakpoint, and the App Store
CTA is live and correct everywhere.

**Surface:** the marketing / SEO / legal surface (server-rendered Jinja, `autoescape=True`), the
SPA shell, the responsive grid.

**Gated on:** Phase 0 client scaffold.

**Key content:**
- Translate `colors.ts` across all 5 themes to CSS custom properties, plus the 3 new token
  families (`--pulse-scrim-*`, `--pulse-on-accent`, `--pulse-on-media`).
- The 6-stop gradient in CSS; the **14-node mesh copied verbatim as inline SVG** from
  `pulseBackground.ts:117-155` — it cannot be reproduced in CSS.
- Breakpoints: phone <768 / tablet 768–1119 / desktop 1120–1599 / wide ≥1600, under the
  governing rule: **the feed column never exceeds 680px at any breakpoint. Extra width buys
  additional columns, never wider rows.**
- Reuse `services/app_links.py` and `templates/_app_link_cta.html` **unchanged**. Macros must be
  imported `with context` or they raise `UndefinedError` at render time.
- **The CTA uses `--pulse-on-accent`, not white** — white on `#32e6b3` measures ~1.8:1 and fails
  WCAG AA.
- One service worker, seeded from `sw.js`'s hardened `safeNotificationUrl()` (`sw.js:186-198`);
  unregister `service-worker.js`. Delete the byte-identical `site.webmanifest`.

**Exit:** SPA `index.html` is served from a `/pulse/*` route (**not `/static/`**, which has no
CSP). `script-src` has **no `'unsafe-inline'`** on the SPA surface. Reduced-motion and
high-contrast blocks mirror native `theme.duration()` / `HIGH_CONTRAST_*`. A single service
worker is registered.

**Risk:** three `after_request` hooks currently string-splice tokens, favicon and i18n into HTML
response bodies. They will also splice into the SPA's `index.html`. Decide explicitly whether
the SPA opts out or consumes them.

---

## 4. Phase 2 — Authentication

**Goal:** a user can sign up, sign in, recover, manage devices and sessions, and sign out — in a
browser.

**Surface:** `/login`, `/signup`, recovery, `/api/account` (16 routes, **13 with no web caller
today**).

**Gated on:** all of Phase 0's auth workstream.

**Key content:**
- The cookie leg already exists. `/login` already mints both a session cookie and bearer/refresh
  tokens in one handler.
- The web client mints a stable random per-browser `X-PulseSoc-Device-Id`, persists it in
  `localStorage`, and sends it on every request — this makes the existing limiter's user and
  device legs work and gives the session table a meaningful `device_label` for web.
- Browser step-up/re-auth for 2FA. The native flow assumes a native prompt; this is the one auth
  area genuinely unbuilt for web rather than merely unexercised.
- The refresh cookie rotates. **Any web concurrency — two tabs, a prefetch, a parallel XHR
  burst — hits the reuse-detection path.** Test it deliberately.

**Exit:** sign-in, refresh-under-concurrency, revoke-a-device and sign-out all pass against a
live environment. The session list shows meaningful per-browser entries.

**Risk:** **the "sign out everywhere" decision must be made here, not later.** The Flask cookie
session has no server-side row and `PERSISTENT_SESSION_DAYS = max(3650, env)` is a 10-year
floor the env var can only lengthen. Either add a server-side session record, or accept it — and
if accepting, **the UI must not display a promise the backend cannot keep.**

---

## 5. Phase 3 — Pages (the proof phase)

**Goal:** prove the entire client architecture end to end — auth → fetch → render → mutate —
without touching money, media, realtime or a protected system.

**Surface:** `/api/pages` — **21 routes, 16 native callers, zero web callers.** 7 native screens:
`PagesHubScreen`, `PageScreen`, `PageCreate`, `PageEdit`, `PageTeam`, `PageConnections`.

**Gated on:** Phase 2.

**Why here:** plain JSON CRUD. No backend change, no native-only mechanism, no upload
dependency, no realtime requirement. **The cleanest, lowest-risk parity win in the entire
inventory**, and therefore the right place to discover that an architectural assumption is
wrong.

**Exit:** full CRUD against a live environment, at all four breakpoints. Error and empty states
**never co-render** — a failed fetch is not empty data, and the mutual exclusion is tested.
`pulseApi`'s `error_code` contract (not `code`) is honoured.

**Risk:** low by construction. If this phase is hard, the architecture is wrong — which is the
point of running it early and cheap.

---

## 6. Phase 4 — Profile and Settings

**Goal:** view and edit a profile; use settings as a two-pane master/detail.

**Surface:** `/api/pulse` profile endpoints (4 web paths, **0 of 6 endpoints called today**);
the `settings/<id>` registry; `user_settings`, `notification_preferences`, `privacy_preferences`.

**Gated on:** Phase 3. Avatar and cover upload are gated on **Phase 7** and ship then.

**Key content:**
- `users` is a **106-column god table**. The API must return a projection; the client must never
  receive the row. This is an API-layer change, not a schema change.
- `/@username` depends on the Phase 0 index.
- Web-only preferences need **no schema change at all** — `user_settings` is key/value with
  `UNIQUE (user_id, setting_key)`. New keys, no DDL.
- Desktop is genuinely better here: two-pane over the settings registry.

**Exit:** profile renders and edits; settings read/write round-trips; no over-fetch of the 106
columns at the wire.

---

## 7. Phase 5 — Feed, posts, comments

**Goal:** the core social product works in a browser.

**Surface:** `/api/pulse/posts` — 11 rules, **the single most-shared family between web and
native** (6 endpoints already overlap).

**Gated on:** **the `user_id = 0` decision.** This is a hard gate.

> **81% of `pulse_posts` rows (1,915 posts) carry `user_id = 0`, a sentinel with no matching
> user.** A feed query written the obvious modern way — `INNER JOIN users ON posts.user_id =
> users.id` — **silently drops 81% of the feed.** No error, no warning. The current code works
> because it resolves authorship in Python rather than joining.

Preferred resolution: insert a real system user with id 0 and keep the sentinel meaningful —
cheapest and safest, with the caveat that an explicit-id `INSERT` must not desync
`users_user_id_seq`. This also unblocks the `pulse_posts.user_id` foreign key.

**Key content:**
- **Move to keyset pagination.** The current feed uses `OFFSET` with a computed `ORDER BY`, and
  browsers deep-paginate far more than phones do.
- `media_ids_json` is TEXT with resolution in Python at `services/pulse_feed_engine.py:589`. The
  API returns resolved media; **the web client never sees the column.**
- Desktop: a third column keeps a thread open beside the feed.

**Exit:** feed row count matches the native app's for the same account. Keyset pagination is
stable under insertion. The 11 core social-graph FKs are added and validated.

**Risk:** the highest-consequence silent failure in the project. Assert on **row counts against
native**, not on "the page rendered."

---

## 8. Phase 6 — Notifications

**Goal:** the bell works, and deep links resolve to the right destination.

**Surface:** 4 already-shared endpoints — the best-covered area after posts.

**Gated on:** **the notifications lineage decision.** `notifications` (35 columns, 60 MB,
**62,279 rows**) vs `pulse_notifications` (**12,895 rows**) vs `notification_*` (10 tables) vs
`pulse_notification_*` (3). This must be settled before the bell is built, not during.

**Key content:**
- `entity_id` is **TEXT and polymorphic**. Deep-link resolution happens in Python; the API
  returns a resolved URL. A wrong resolution is a wrong destination, silently.
- Web Push (VAPID) already exists and works — wire it for the out-of-tab case.
- Notification-click routing goes through the single hardened service worker from Phase 1.

**Exit:** every notification type resolves to a correct destination, enumerated and tested per
type. Web Push delivers with the tab closed.

---

## 9. Phase 7 — Media pipeline

**Goal:** a browser can upload media, correctly and directly.

**Surface:** presigned R2 multipart — init → presigned part URLs → complete
(`services/media_upload_sessions.py:142, 176, 211`).

**Gated on:** Phase 0's R2 CORS verification. **Nothing in this phase works without
`ExposeHeaders: ["ETag"]`** — `fetch` cannot read the `ETag` of a cross-origin upload response
without it, so every part returns `null` and the complete call fails validation at `:212`.

**Key content:**
- **Upload direct to R2. Never proxy through Flask** — the app's POST cap is far below the
  direct path.
- `Blob.slice()` chunking, resume, and an explicit progress control.
- **No browser equivalent of a native background task exists.** The tab must stay open, and the
  UI must say so rather than implying otherwise.

**Exit:** a large multi-part upload completes, resumes after an interruption, and produces a
media record identical in shape to the native app's.

**Risk:** this phase unblocks Phases 8, 12 and 13 simultaneously. A slip here is a four-way
slip.

---

## 10. Phase 8 — Reels, Status, Create

**Goal:** the media-native parts of the product work on web, adapted rather than copied.

**Surface:** `/api/pulse/reels` (21 routes, **11 native callers, zero web**); status (3 paths, 2
of 5 endpoints); `CreateTabScreen`.

**Gated on:** Phase 7.

**Key content:**
- Reels: vertical full-screen → **centred 9:16 player with side comments.** Keyboard and pointer
  navigation are mandatory, not optional.
- Status/stories: tap-hold → click/keyboard, with an **explicit progress control**. Use
  `pulse_status` (51 rows); `pulse_statuses` and `pulse_stories` are near-empty.
- Create: camera capture → file picker + `getUserMedia`; full-screen modal → centred dialog.
- Lock-screen now-playing is Swift (`modules/pulse-now-playing/`); the Media Session API is a
  partial analogue only.

**Exit:** playback, creation and navigation all work by keyboard alone.

---

## 11. Phase 9 — Messages

**Goal:** text messaging in a persistent two-pane desktop layout.

**Surface:** `/api/pulse/messages` (29), `/api/messages` (18, media init/upload/complete).

**Gated on:** the `comm_v2_*` lineage decision (**`comm_v2_messages` = 1,099 rows** vs
`pulse_messages` = 29 — use `comm_v2_*`), and a status ruling on
`/api/pulse/communications` (162-route blueprint, no resolvable caller on either side).

> **Tell-tale of a dead lineage:** `pulse_messages` carries **8 indexes, two of them exact
> duplicates, for 29 rows.** Index count is a fossil of intent, not of use.

**Key content — the constraint, stated as a decision:**

**Realtime is polling, deliberately.** `bot.py:89367`: *"Long-lived browser streams can exhaust
the main Gunicorn worker pool."* Both SSE routes return `204` with
`X-Pulse-Realtime-Transport: polling`. There is no WebSocket server, and the topology
(`--workers 4 --threads 8`) forbids one — 32 concurrent streams is the entire capacity of the
service.

**This phase is scoped against polling.** Changing it means changing the deployment topology,
which is a separate project. Design consequences: optimistic local echo, an explicit send-state
(not a spinner), visible-tab polling with backoff when hidden, and **Web Push for the
not-looking-at-the-tab case that polling handles worst.** Presence, typing and read receipts
ship at reduced fidelity, explicitly.

**Exit:** two-pane desktop layout; message send/receive round-trips; the latency floor is
visible in the UI's design rather than hidden behind a spinner.

---

## 12. Phase 10 — Search and Saved

**Goal:** Saved works. Search becomes a product.

**Surface:** Saved — 1 path, **0 of 5 endpoints**, clean JSON, low risk. Search — `/search`.

**Gated on:** **a product decision on search.** This is not an engineering gate.

> `/search` currently term-matches **139 static SEO marketing pages**, and its FAQ still
> hard-codes CoinPlotXAI seed-phrase questions from the crypto-bot era. On the backend, search
> is 3 modules / ~160 lines of `LIKE '%x%'` plus 29 lines of Python scoring — **no full-text
> index, no trigram index, nothing.**
>
> **Search does not exist as a product on either platform.** Building the web to match native
> search would be building nothing. Postgres FTS? Trigram? An external index? That decision
> precedes any work here.

`pulse_saved_items` uses polymorphic `content_type`/`content_id` TEXT — each target resolves
per-type in Python, at the API layer. Also drop its duplicate UNIQUE indexes (Phase 0).

**Exit:** Saved round-trips for every content type. Search ships against whatever the product
decision selected — and `/search*` is in the AASA, so its app-intent behaviour is classified
explicitly.

---

## 13. Phase 11 — Marketplace browse and cart

**Goal:** browse, view listings, and build a cart.

**Surface:** `/api/pulse/marketplace` — 32 rules, **19 native callers, 0 web.**
`MarketplaceScreen`, `MarketplaceProductScreen`, `MarketplaceCartScreen` (513 lines).

**Gated on:** Phase 5 (shared components), Phase 7 (listing media).

**Key content:**
- Desktop earns a genuinely better experience: **a 3–4 column grid with a persistent facet panel
  beats a filter sheet.**
- `/api/pulse/marketplace` (32) and `/api/business-os/marketplace` (28) are **different products
  sharing a noun** — which is worse than a straight duplicate, because it reads as one. Do not
  cross them.
- **The type mismatch is live here.** `marketplace_*` uses **integer** ids; `business_os_mkt_*`
  uses **text**. Local dev and the test suite run on SQLite, which happily compares the two.
  **Production Postgres raises.**

**Exit:** browse, filter and cart round-trip. A test asserts on the **query binding type**, not
on "the operation succeeded" — the latter passes under SQLite regardless.

---

## 14. Phase 12 — Checkout, orders, payments

**Goal:** the money path.

**Surface:** `MarketplaceCheckoutScreen` (890 lines, 8 API modules), offers, returns,
`/api/pulse/payments` (13 rules, 8 native callers), `BuyerOrdersScreen`, `OrdersManagerScreen`.

**Gated on:** Phase 11. Phase 0's type-binding test. **No backend work.**

> **The single biggest piece of good news in the inventory, and it is invisible unless you read
> `mobile-native/src/api/stripePaymentSheet.ts`:**
> `/api/pulse/marketplace/cart/checkout` and `/api/pulse/payments/checkout` **already return a
> PaymentIntent client secret** when asked for `payment_mode: "payment_sheet"` — precisely the
> shape Stripe.js and Stripe Elements need in a browser.
>
> **The web checkout does not require a new backend endpoint. It requires a different client SDK
> against the same response.**

**Apple IAP is 🚫 not applicable and none should be built.** Apple requires IAP for digital goods
in an iOS app; the web is under no such obligation and Stripe is cheaper. **Both verify paths
already write the same entitlement record**, so the two reconcile server-side today.

**Exit:** a real purchase completes end to end against Stripe test mode, including offers and
returns. `form-action` CSP remains pinned to `checkout.stripe.com`. Both entitlement paths are
verified to converge.

**Risk:** the highest-consequence phase in the project. The SQLite-vs-Postgres type trap sits
directly on it — **a checkout path that crosses from `marketplace_*` into `business_os_mkt_*`
can pass every local test and fail in production on the money path.**

---

## 15. Phase 13 — Business OS, seller, store, dashboards

**Goal:** the seller and business console, where desktop genuinely wins.

**Surface:** `/api/business-os` (174 rules), `SellerApplicationScreen` (**1,153 lines** — the
largest single screen port in commerce), `StoreDashboardScreen` (1,144), `BusinessOsPayments`,
`/dashboard` (45 paths, 13 of them dashboards).

**Gated on:** Phase 7 (seller-application document upload), Phase 12.

**Key content:**
- Business OS has a separate authz model. `enforce_private_business_os_authentication_boundary`
  401s **every** `/api/business-os/*` path without an authenticated account.
- The commerce gateway's `_verified_bearer_write_authority()` local fix becomes redundant once
  Phase 0 lifts it into `account_user_id()` — remove it deliberately, do not leave two.
- 13 dashboard paths stop being a menu and become **true multi-column**.

**Exit:** seller application submits with documents; store dashboard renders; order management
works both sides.

---

## 16. Phase 14 — Ads platform (web-first)

**Goal:** a campaign builder that is **better than the phone's**, not a port of it.

**Surface:** `/api/pulse/ads` (66 rules, 14 native callers), 10 screens, 13 API modules.

**Gated on:** **the ads-surface decision** — `/api/pulse/ads` (66) vs
`/api/business-os/advertising` (45). Two ads surfaces; which is authoritative must be settled
before either is ported.

**Why web-first:** `AdsCampaignWizardScreen` is **1,985 lines — the largest screen in the app** —
a multi-step wizard over 13 API modules with a policy-review gate and a wallet. A campaign
builder wants a wide canvas, side-by-side targeting and estimate, and a persistent preview.

> **This is the strongest argument in the inventory for "desktop is not a stretched phone," and
> the web UX should deliberately diverge from native here rather than mirroring it.**

The Apple IAP wallet top-up leg is not applicable to web; Stripe is the web path.

**Exit:** a campaign can be created, funded, reviewed and launched from the desktop UI in fewer
steps than the native wizard.

---

## 17. Phase 15 — Intelligence, and GA hardening

**Goal:** the remaining product areas, then general availability.

**Surface:**

| Area | Scope on web |
|---|---|
| **Private Office** (61 routes, 13 screens) | **Port read surfaces.** Must implement the header-bound `X-Office-Grant` / `X-Office-Device` handshake and the `423` unlock flow — **not route around it**. `/api/private-office/meetings` (26 routes, no resolvable caller either side) needs a status ruling first |
| **Capital graph** (7 endpoints) | Read-only. Financial data. **Render, never pair with an action that reads as advice or execution** |
| **Pulse AI** (16 routes, 4 native-called) | Port **with the `actions/confirm` + `actions/cancel` control intact.** It is a safety control; flattening it into an optimistic "the assistant did it" toast — the natural web implementation — removes it. **No AI provider key in the bundle**, ever |
| **UNDX** (27 endpoints) | **Read-only in phase 1.** Receipts, run history, capability listing. `APPROVE UNDX WRITE` and `APPROVE UNDX GUARD CHANGE` typed into a browser are materially weaker controls; emergency-stop deserves a design conversation, not a port |
| **Briefings** (3) | Port; low risk |
| **Crypto** (25) | Live subsystem, demoted to a premium sub-product |

**Then the GA gate:** all 15 rows of `PULSESOC_WEB_SECURITY_MODEL.md` §11 green, and every
in-scope row of `PULSESOC_WEB_TEST_AND_PARITY_PLAN.md` passing against a live environment.

**Gated on:** everything above.

---

## 18. Excluded for the duration

**⛔ Protected systems — inventory only, no web work, no modification:** livestream audio,
livestream infrastructure, audio calls, video calls, call audio routing, the Agora session
foundation, Pulse Radio audio foundation. `/api/pulse/live` (38 routes) and `/api/calls` (24)
are out of scope in every phase.

The characteristic failure here is silent: an unrelated surface steals the audio session, the
build stays green, tests pass, and production goes quiet.

**Groups** (45 routes, ~0 callers on **either** client) is not scheduled. Do not port an unwired
feature — establish whether it is a live product first. If it is, it enters after Phase 10.

---

## 19. The decision calendar

Every decision, and the phase it blocks. **Each is free to make now and expensive to make late.**

| Decision | Blocks | Latest |
|---|---|---|
| Web session revocability (fix or formally accept) | Security sign-off | **Phase 2** |
| `user_id = 0` — system user, backfill, or mandated `LEFT JOIN` | Feed **and** the `pulse_posts` FK | **Phase 5** |
| Notifications lineage (`notifications` 62,279 vs `pulse_notifications` 12,895) | The bell | **Phase 6** |
| Messages lineage (`comm_v2_*`) + status of `/api/pulse/communications` | Messages | **Phase 9** |
| Friends lineage · Mutes lineage (both near-empty) | Social graph surfaces | **Phase 9** |
| Search: FTS, trigram, or external index? | Search | **Phase 10** |
| Groups: is it a live product? | Whether Groups is in scope at all | Before scheduling |
| Sellers/orders: `marketplace_*` (int) vs `business_os_mkt_*` (text) | Checkout | **Phase 11** |
| Rooms lineage (`pulse_room_*` text vs `arena_room_*` int — Arena is legacy) | — | With W-B |
| Ads surface: `/api/pulse/ads` vs `/api/business-os/advertising` | Ads | **Phase 14** |
| `/api/private-office/meetings` status | Private Office | **Phase 15** |
| Deletions (`/api/arena` 157, `/api/pulse/mobile` 30, `/api/reels` 12, …) | Cleanup | **Gated on W-B evidence** |

---

## 20. What this plan is not

- **Not an estimate.** No durations, no story points. The inventory supports ordering and
  dependency claims and nothing more.
- **Not a parity claim.** Every 🟢 in the parity matrix is a capability judgement from code
  tracing. **No endpoint was called.** They become real one row at a time in
  `PULSESOC_WEB_TEST_AND_PARITY_PLAN.md`.
- **Not a deletion list.** W-B gates every deletion on production evidence.
- **Not a backend rewrite.** The backend workstream is 10 items (§2 and
  `PULSESOC_WEB_TARGET_ARCHITECTURE.md` §8). **236 endpoints need callers, not construction.**

---

## Cross-references

- `PULSESOC_WEB_TARGET_ARCHITECTURE.md` — the architecture each phase builds against
- `PULSESOC_WEB_SECURITY_MODEL.md` §11 — the launch gate referenced by Phase 15
- `PULSESOC_NATIVE_TO_WEB_PARITY_MATRIX.md` — the capability target per phase
- `PULSESOC_WEB_API_GAP_ANALYSIS.md` — the endpoints behind each surface
- `PULSESOC_DATABASE_GAP_ANALYSIS.md` — every decision in §19
- `PULSESOC_WEB_TEST_AND_PARITY_PLAN.md` — how each Exit is proven
