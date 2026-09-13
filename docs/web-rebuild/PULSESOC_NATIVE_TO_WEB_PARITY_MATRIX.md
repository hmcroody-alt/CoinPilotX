# PulseSoc — Native → Web Parity Matrix

**Source of truth: the native app.** This document maps what the native app *is* onto what the
website would have to become. It does not treat the existing website as a specification.

Scope: 182 native routes / 159 registered screen names / 147 `*Screen.tsx` files, against 172
existing `/pulse/*` web paths and the 45-endpoint shared API surface.

---

## 0. The finding that reframes "parity"

The website already has pages named after almost every native destination:

| Native tab noun | Existing `/pulse/*` web paths |
|---|---:|
| dashboard | 13 |
| live | 7 |
| settings | 7 |
| messages | 5 |
| profile | 4 |
| marketplace | 3 |
| status | 3 |
| groups | 3 |
| reels | 2 |
| create | 2 |
| saved / search / notifications / ai | 1 each |

And yet **web and native share only 45 API endpoints**, and the web inventory found that
**126 page routes — 25% of all HTML routes — execute no data access at all**.

> **URL coverage is not parity.** The website has a page called `/pulse/marketplace`; it calls
> none of the 19 marketplace endpoints the native app uses. The gap is not missing pages. It is
> pages that do not do anything.

This is why the rebuild cannot be scoped by counting missing URLs. It must be scoped by counting
missing *behaviour*, which is what the 236-endpoint figure in
`PULSESOC_WEB_API_GAP_ANALYSIS.md` measures.

---

## 1. Legend

| Mark | Meaning |
|---|---|
| 🟢 **Full** | Web can do everything native does |
| 🟡 **Adapted** | Same capability, deliberately different UX on web |
| 🟠 **Reduced** | Web does less, by constraint (not by choice) |
| 🔴 **None** | No web equivalent today; must be built |
| ⛔ **Excluded** | Protected system — inventory only, no web work proposed |
| 🚫 **Not applicable** | Platform-specific by nature; web should *not* have it |

---

## 2. Tab-level parity (the 15 registered tabs)

| Native tab | Screen | Web today | Target | Notes |
|---|---|---|---|---|
| Home | (inline) | page exists, feed partially wired | 🟢 Full | The 6 shared `/api/pulse/posts` endpoints are the one genuinely working overlap |
| Search | `SearchScreen` | 🔴 `/search` searches **139 static SEO pages**, not PulseSoc | 🟠 Reduced | **Search does not exist as a product.** `LIKE '%x%'` + 29 lines of Python scoring, no FTS/trigram anywhere. See §6 |
| Reels | `ReelsScreen` | 🔴 none of 21 endpoints called | 🟡 Adapted | Vertical full-screen → centred 9:16 player + side comments; keyboard/pointer nav mandatory |
| Create | `CreateTabScreen` | partial | 🟡 Adapted | Camera capture → file picker + `getUserMedia`; full-screen modal → centred dialog |
| Status | `StatusScreen` | 3 paths, 2 of 5 endpoints | 🟡 Adapted | Stories: tap-hold → click/keyboard; needs explicit progress control |
| Messenger | `MessengerScreen` | 5 paths, text only | 🟠 Reduced | **Realtime is polling by design** (§5). Media init/upload/complete unported |
| Notifications | `ActivityInboxScreen` | 4 shared endpoints | 🟢 Full | Best-covered area after posts |
| Marketplace | `MarketplaceScreen` | 3 paths, **0 of 19** endpoints | 🟢 Full | Backend already returns a Stripe client secret — see §3 |
| Profile | `ProfileScreen` | 4 paths, 0 of 6 endpoints | 🟢 Full | Avatar/cover upload blocked on R2 CORS |
| Saved | `SavedScreen` | 1 path, 0 of 5 endpoints | 🟢 Full | Clean JSON; low risk |
| Groups | `GroupsScreen` | 3 paths | 🟠 Reduced | **Largely unwired on _both_ clients** — 45 routes, ~0 native callers. Establish product status first |
| Live | `LiveScreen` | 7 paths | ⛔ Excluded | Protected system |
| PulseAI | `PulseAiScreen` | 1 path | 🟡 Adapted | Port **with** the confirm/cancel action control intact |
| Dashboard | `UserDashboardScreen` | 13 paths | 🟢 Full | Desktop is genuinely better here (multi-column) |
| Settings | `SettingsScreen` | 7 paths | 🟢 Full | `settings/<id>` registry maps cleanly to a two-pane master/detail |

---

## 3. Commerce parity

| Capability | Native | Web target | Blocker |
|---|---|---|---|
| Browse / listing detail | `MarketplaceScreen`, `MarketplaceProductScreen` | 🟢 Full | none |
| Cart | `MarketplaceCartScreen` (513 ln) | 🟢 Full | none |
| **Checkout** | `MarketplaceCheckoutScreen` (890 ln), Stripe PaymentSheet | 🟢 **Full** | **none — backend already returns a PaymentIntent client secret for `payment_mode: "payment_sheet"`; Stripe.js consumes the identical response** |
| Offers (counter/accept) | `marketplaceOffers.ts` | 🟢 Full | none |
| Returns | `marketplace_returns_routes.py` | 🟢 Full | none |
| Seller application | `SellerApplicationScreen` (1,153 ln) | 🟢 Full | **R2 CORS must expose `ETag`** for document upload |
| Seller store / dashboard | `StoreDashboardScreen` (1,144 ln) | 🟢 Full | none |
| Orders (buyer + seller) | `BuyerOrdersScreen`, `OrdersManagerScreen` | 🟢 Full | none |
| Payouts / Connect | `BusinessOsPaymentsScreen` | 🟢 Full | none |
| **Apple IAP** (premium, ads wallet) | `expo-iap` ^4.3.1 | 🚫 **Not applicable** | Web uses Stripe Checkout. Both verify paths already write the same entitlement record |

**Commerce is the best-prepared area in the entire inventory** — 19 endpoints with zero web
callers, but no backend work required for the money path.

---

## 4. Business, growth & intelligence parity

| Area | Native | Web target | Notes |
|---|---|---|---|
| **Pages** | 7 screens, 21 endpoints | 🟢 **Full** | **Cleanest win available.** Plain JSON CRUD, no uploads, no realtime, no native mechanism. The ideal first non-trivial phase |
| Ads platform | 10 screens, 66 endpoints | 🟡 Adapted | `AdsCampaignWizardScreen` is 1,985 lines — the largest screen in the app. Desktop is *better* than phone here; treat as **web-first**, not a port |
| Business OS | 6 screens, 174 endpoints | 🟢 Full | Separate authz model |
| Creator / growth | 2 screens | 🟢 Full | |
| Private Office | 13 screens, 61 endpoints | 🟢 Full | Must implement the header-bound `423` second-lock handshake, not route around it |
| Capital graph | 7 endpoints | 🟢 Full (read-only) | Financial data. Render, never pair with an action that reads as advice |
| Pulse AI | 16 endpoints | 🟡 Adapted | `actions/confirm` + `actions/cancel` is a **safety control** — do not flatten into an optimistic toast |
| **UNDX** | 27 endpoints | 🟠 **Deliberately reduced** | **Read-only on web in phase 1.** Approval phrases (`APPROVE UNDX WRITE`, `APPROVE UNDX GUARD CHANGE`) and emergency-stop are weaker controls in a browser |
| Briefings | 3 endpoints | 🟢 Full | |
| Crypto | 25 endpoints | 🟢 Full | Live subsystem, demoted to premium sub-product |

---

## 5. Capabilities that cannot reach full parity

These are constraints, not backlog items. Each is a decision the architecture must absorb.

| Capability | Why web is limited | Verdict |
|---|---|---|
| **Voice / video calls** | Protected system. Web has **zero** `RTCPeerConnection` and zero `new WebSocket` today. RTC is Agora | ⛔ Out of scope |
| **Livestreaming** | Same | ⛔ Out of scope |
| **Realtime messaging** | `bot.py:89367`: *"Long-lived browser streams can exhaust the main Gunicorn worker pool."* Both SSE routes return `204` + `X-Pulse-Realtime-Transport: polling`. No WebSocket server exists | 🟠 Polling only — a **deployment-topology** decision, not a sprint |
| Presence / typing / read receipts | Same polling constraint | 🟠 Reduced fidelity |
| Push notifications | Web Push (VAPID) exists and works; APNs/FCM are native by definition | 🟡 Adapted |
| Background upload | No browser equivalent of a native background task | 🟠 Tab must stay open |
| Biometric unlock | WebAuthn is the analogue, not an equivalent | 🟡 Adapted |
| QR scanning | `BarcodeDetector` + `getUserMedia`; uneven browser support | 🟡 Adapted, with fallback |
| Lock-screen now-playing | `modules/pulse-now-playing/` is Swift; Media Session API is a partial analogue | 🟠 Reduced |
| Apple IAP | Platform requirement for iOS only | 🚫 Not applicable |
| Haptics | No equivalent | 🚫 Not applicable |

---

## 6. Two areas where "parity" is the wrong goal

**Search.** `/search` currently term-matches 139 static SEO marketing pages, and its FAQ still
hard-codes CoinPlotXAI seed-phrase questions from the crypto-bot era. On the backend, search is
3 modules / ~160 lines of `LIKE '%x%'` plus 29 lines of Python scoring — no full-text index, no
trigram index, nothing. **Search does not exist as a product on either platform.** Building the
web to match native search would be building nothing. This needs a product decision (Postgres
FTS? external index?) before it can be a parity item at all.

**Groups.** 45 routes, essentially no callers on either client. Do not port an unwired feature —
establish whether it is a live product first.

---

## 7. Where web should deliberately exceed native

The mission requires desktop to be a premium experience, not a stretched phone. These are the
places where the *native* app is the constrained one:

| Surface | Why desktop wins |
|---|---|
| Ads campaign wizard | Wide canvas; side-by-side targeting + estimate + persistent preview instead of a 1,985-line multi-step drilldown |
| Business OS / dashboards | True multi-column; 13 dashboard paths stop being a menu |
| Marketplace browsing | 3–4 column grid + persistent facet panel beats a filter sheet |
| Messages | Persistent two-pane list + thread; native's "back" has no desktop equivalent |
| Settings | Two-pane master/detail over the `settings/<id>` registry |
| Private Office | Document + record review benefits most from screen area |
| Post + thread | Third column keeps the thread open beside the feed |

**The governing rule** (from the design system map §10.2): *the feed column never exceeds 680px
at any breakpoint. Extra width buys additional columns, never wider rows.*

---

## 8. Parity scoring

| Verdict | Areas |
|---|---:|
| 🟢 Full parity achievable | 21 |
| 🟡 Adapted (same capability, different UX) | 9 |
| 🟠 Reduced by constraint | 8 |
| ⛔ Excluded (protected) | 2 |
| 🚫 Not applicable to web | 3 |

**Read this as: roughly 70% of the native product can reach full or adapted parity on web with
no backend change, because the endpoints already exist and are exercised daily by the phone.**
The reduced set is dominated by a single root cause — the polling realtime architecture — and
the excluded set is the protected live/calls foundation.

---

## 9. What this matrix does *not* claim

Per the mission's rule against claiming parity without end-to-end tracing:

- No endpoint was **called**. Route existence is not proof of correct behaviour, auth, or
  response shape.
- Parity marks are **capability** judgements from code tracing, not verified user journeys.
- Method-level granularity is collapsed (`GET /x` and `POST /x` count once), so endpoint counts
  are a floor on implementation effort.
- Every 🟢 in this document becomes real only when the corresponding row in
  `PULSESOC_WEB_TEST_AND_PARITY_PLAN.md` passes against a live environment.

---

## Cross-references

- `PULSESOC_WEB_API_GAP_ANALYSIS.md` — the 236 endpoints behind every 🔴
- `PULSESOC_NATIVE_PRODUCT_AREAS.md` — the screens being mapped
- `PULSESOC_WEB_DESIGN_SYSTEM_MAP.md` §10 — responsive strategy behind every 🟡
- `PULSESOC_REUSE_VS_REBUILD_MATRIX.md` — what of the existing web survives
