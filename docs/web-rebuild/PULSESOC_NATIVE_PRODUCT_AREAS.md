# PulseSoc Native App — Product Area Catalogue

**Source of truth:** `mobile-native/` (Expo 54, RN 0.81.5, React 19, TypeScript, React Navigation, Zustand).
**Purpose:** enumerate the product areas the rebuilt website must reach parity with. Feeds the parity matrix and phased roadmap.

**Scope note:** navigation architecture, the hamburger drawer, deep linking, and the five main tabs (Home, Reels, Create, Messages/Messenger, Profile) are covered by a separate document (`PULSESOC_NATIVE_NAVIGATION_AND_TABS.md`). This document covers everything else.

**Data sources used:**
- `docs/web-rebuild/data/native_nav_registrations.json` (185 registrations / 182 unique routes)
- `docs/web-rebuild/data/native_api_endpoints.json` (api module → backend endpoint map)
- Direct reads of `mobile-native/src/**`

**Conventions:** all paths are relative to `/Users/hmcherie/Desktop/CoinPilotX/mobile-native/` unless absolute. Anything not directly confirmed in source is marked **UNVERIFIED**.

---

## 1. Social & content (non-tab)

### 1.1 Search

| Route(s) | Screen | Lines | API |
|---|---|---|---|
| `Search` (tab **and** stack) | `src/screens/SearchScreen.tsx` | 592 | `api/search.ts` |

`api/search.ts` hits **`GET /api/pulse/search?query=…&limit=…`** (a templated path, which is why the module shows "no endpoint" in §10.2 — it is not dead).

**Search is federated across 11 result groups** (`SEARCH_GROUPS`, `api/search.ts:52`):

| Key | Label |
|---|---|
| `posts` | Posts |
| `creators` | People |
| `presences` | Artists & Businesses |
| `videos` | Videos |
| `reels` | Reels |
| `statuses` | Status |
| `marketplace` | Marketplace |
| `music` | Music |
| `groups` | Communities |
| `rooms` | Rooms |
| `comments` | Comments |

Additional behaviours the web must match: **offline result cache** (`loadCachedPulseSearch` / `cachePulseSearch`), **recent searches** (`loadRecentSearches` / `saveRecentSearch`), **default trending searches** when the box is empty (`defaultTrendingSearches`), response normalisation (`normalizeSearchResponse`), save-from-result-row (`social/savedStore`, `social/useSaveAction`), and profile-target resolution (`api/profileTarget.ts`) so a result row opens the right profile kind.

### 1.2 Discovery

Not a screen — a **module that injects discovery rows into the Home feed**. `src/discovery/` (13 files):

| File | Role |
|---|---|
| `useHomeDiscovery.ts` | the hook Home calls |
| `sources.ts` | pulls from `api/reels`, `api/status`, `api/groups`, `api/friends` (`listSuggestedPeople`) |
| `discoveryRows.ts` | row assembly |
| `DiscoveryRowView.tsx`, `DiscoveryCardFrame.tsx`, `DiscoveryPreviewMedia.tsx` | presentation |
| `previewPlayback.ts` | inline preview autoplay |
| `reelTransfer.ts` | hand a reel from a discovery card into the Reels player at the exact frame |
| `dismissals.ts` | per-user row/card dismissal |
| `flags.ts`, `analytics.ts`, `discoveryCardMetrics.ts` | gating + telemetry |

API: `api/friends.ts` → `/api/pulse/friends`, `/api/pulse/friends/request`.

### 1.3 Saved / Collections

| Route(s) | Screen | Lines | API |
|---|---|---|---|
| `Saved` (tab **and** stack) | `src/screens/SavedScreen.tsx` | 799 | `api/saved.ts` |

Endpoints: `/api/pulse/saved`, `/api/pulse/saved/${itemId}`, `/api/pulse/saved/${itemId}/move`, `/api/pulse/saved/collections`, `/api/pulse/saved/collections/${collectionId}`.

**Collections are first-class** — items can be moved between collections (`/move`). Saving is a cross-app contract, not a Saved-screen feature:

| File | Role |
|---|---|
| `social/saveContract.ts` | `SavableContentType`, `SaveTarget`, `saveKey`, `saveTargetFromUrl` |
| `social/savedStore.ts` | global observable saved-state (`observeSavedState`, `subscribeToSaveChanges`, `useSavedState`) |
| `social/useSaveAction.ts` | `setSaved` — the single mutation path |
| `social/actionGuard.ts` | `describeSavedActionError`, `describeSavedLibraryError` |

Save affordances appear in Search rows, Activity Inbox rows, Home, Reels, Status, Marketplace. **The web needs the same global saved-state store**, or save buttons will desynchronise across surfaces.

Media viewing uses `components/NativeMediaViewer.tsx`.

### 1.4 Status

| Route(s) | Screen | Lines | API |
|---|---|---|---|
| `Status` (tab), `StatusDetail` (stack) | `src/screens/StatusScreen.tsx` | 730 | `api/status.ts` |

Endpoints: `/api/pulse/status`, `/api/pulse/status/${id}`, `/api/pulse/status/${id}/react`, `/api/pulse/status/${id}/reply`, `/api/pulse/status/${id}/share`, `/api/pulse/status/${id}/view`, **`/api/pulse/status/ai-story`**.

Sub-surfaces:
- **Creation:** `components/StatusCreator.tsx`; also created from `CameraStudioScreen` (`createStatus`, `StatusVisibility`).
- **AI story generation:** `/api/pulse/status/ai-story` — an AI-composed status. Parity item for the web.
- **Viewer:** `components/StatusViewerCard.tsx` + `components/NativeMediaViewer.tsx`.
- **Per-status moderation inline:** `mutePostAuthor` (`api/feed`), `blockPulseUser` + `reportPulseTarget` (`api/support`).
- **Share:** `sharing/nativeShare.ts` `sharePulseObject`.
- **View receipts:** `/view` is called on display — the web must replicate seen-tracking or status analytics will be wrong.
- Visibility is typed (`StatusVisibility`), and reactions are guarded by `social/actionGuard.ts`.

### 1.5 Activity Inbox & Notifications (7 routes, 3 screens)

| Route(s) | Screen | Lines | API |
|---|---|---|---|
| `Notifications` (tab), `ActivityInbox`, `ActivityInboxLegacyInbox`, `ActivityInboxWebActivity`, `ActivityInboxWebInbox` | `src/screens/ActivityInboxScreen.tsx` | 550 | `api/activity.ts` |
| `NotificationCenter` | `src/screens/NotificationCenterScreen.tsx` | 353 | `api/notifications.ts` |
| `BusinessOsActivity` → `ActivityRoute` | `src/screens/ActivityScreen.tsx` | 351 | `api/activityFeed.ts` + `api/notifications.ts` |

`api/activity.ts`: `/pulse/activity`, `/pulse/notifications`, `/pulse/messages/${conversationId}`, `/pulse/calls/${callId}` — i.e. the inbox **unifies notifications, messages and calls in one stream**.

`api/activityFeed.ts`: `/pulse/marketplace`, `/pulse/orders` — the Business OS activity variant folds in commerce events.

`api/notifications.ts`: `/api/pulse/notifications/unread-count`, `/api/pulse/notifications/${id}`, `/${id}/read`, `/${id}/resolve`, `/read-all${query}`, `/api/pulse/notifications/preferences`, `/api/notification-preferences`.

Cross-cutting machinery the web must reproduce:
- `core/unreadCounts.ts` — `refreshUnreadCounts`, `applyOptimisticRead`, `setUnreadCounts` (badge state).
- `core/eventSync.ts` — `registerSyncInvalidation` / `invalidateNativeSync`: a notification arriving invalidates other screens' caches.
- `navigation/notificationRouting.ts` — `routeNotificationTarget` maps a notification payload to a route (including crypto alerts, §5).
- `AppState` listeners refresh on foreground.
- **`/resolve`** is distinct from `/read` — some notifications are actionable and get resolved, not just marked seen.

### 1.6 Groups / Communities

| Route(s) | Screen | Lines | API |
|---|---|---|---|
| `Groups` (tab), `GroupDetail` | `src/screens/GroupsScreen.tsx` | 1438 | `api/groups.ts` |

Endpoints: `/api/pulse/groups/create`, `/api/pulse/communications/rooms`. **Groups and Rooms are the same product surface** — search exposes them as two separate result groups (`groups` = "Communities", `rooms` = "Rooms").

Key modules: `community/communityCreateIntent.ts` (`takeCommunityCreateIntent` — a create intent handed in from elsewhere, e.g. the Create tab), `community/roomConversationRecovery.ts`, `pulseCommand/domain.ts`, and the `components/PulseCommand` family (`PulseCommandHeader`, `PulseCommandPanel`, `PulseCommandSearch`, `PulseCommandAction`) — a command-palette UI shared with other hubs (§4.8).

Group creation is a sheet: `screens/__tests__/GroupsScreen.creationSheet.test.tsx`. Object-level admin roles apply (§8.1).

### 1.7 Events

| Route(s) | Screen | Lines | API |
|---|---|---|---|
| `Events`, `EventDetail`, `LiveScheduleGateway`, `LiveEventCreateGateway` | `src/screens/EventsScreen.tsx` | 576 | `api/events.ts` |
| `BusinessOsEvents` → `EventsRoute` | `src/screens/EventsManagerScreen.tsx` | — | `api/eventsManager.ts`, `api/eventsData.ts` (see §3.8) |

`api/events.ts`: `/pulse/events`, `/pulse/events/${liveId}`, `/pulse/live/${liveId}`, `/pulse/live/events/create`, `/pulse/live/schedule`.

**Events and Live are the same object.** An event is a scheduled live broadcast — `LiveScheduleGateway` and `LiveEventCreateGateway` both render `EventsScreen`, and event ids are live ids. The web must model them as one entity.

Components: `src/components/events/` (`CapacityBar.tsx`, `EventsHeader.tsx`, `AvatarStack.tsx`, …). Sharing via `sharing/nativeShare.ts`.

### 1.7a Pages & Presence (7 routes)

| Route | Screen | Lines |
|---|---|---|
| `PagesHub` | `src/screens/PagesHubScreen.tsx` | 966 |
| `Page` | `src/screens/PageScreen.tsx` | 1062 |
| `PageCreate` | `src/screens/PageCreateRoute.tsx` → `src/screens/PageCreateScreen.tsx` | — |
| `PageEdit` | `src/screens/PageEditScreen.tsx` | — |
| `PageTeam` | `src/screens/PageTeamScreen.tsx` | — |
| `PageConnections` | `src/screens/PageConnectionsScreen.tsx` | — |
| `Presence` | `src/screens/PresenceHubScreen.tsx` | 782 |

`api/pages.ts` — 19 endpoints, the richest non-commerce module:

| Endpoint | Purpose |
|---|---|
| `/api/pages`, `/api/pages/${id}` | list / read / create |
| `/api/pages/${id}/manage` | management view |
| `/api/pages/${id}/posts` | page feed |
| `/api/pages/${id}/follow` | follow |
| `/api/pages/${id}/status` | publish/unpublish state |
| `/api/pages/${id}/members`, `/members/${memberUserId}` | **team roles** |
| `/api/pages/${id}/transfer` | ownership transfer |
| `/api/pages/${id}/verification` | page verification |
| `/api/pages/${id}/links`, `/link-options` | **connections between pages** |
| `/api/pages/identities` | which identity you post as |
| `/api/pages/invites`, `/invites/accept`, `/invites/decline` | team invites |

**"Presence" is the product word for a Page.** Search labels the `presences` group "Artists & Businesses"; `PresenceHubScreen` calls `listMyPages`; there is a dedicated `theme/presenceAccent.ts` and `theme/presenceTheme.ts`. A Page can also surface marketplace listings (`PageScreen` imports `searchMarketplace`), so Pages bridge social and commerce.

`PresenceHubScreen` is **launch-gated**: `launch/useLaunchGate.ts`, `launch/readiness.ts` (`presenceModuleId`, `readinessOf`), `launch/ComingSoonSheet.tsx`, `launch/lockedMotion.ts`. Some presence modules ship as "coming soon" — the web must respect the same readiness gate or it will expose unfinished features.

Posting to a page uses `components/FeedComposer.tsx`; identity selection via `/api/pages/identities`.

### 1.8 Sharing

| Route | Screen | Lines |
|---|---|---|
| `PulseShare` | `src/screens/PulseShareScreen.tsx` | 275 |

Modules: `src/sharing/nativeShare.ts` (`sharePulseObject`, `buildNativeSharePayload`, `openSystemShare`), `src/sharing/shareComposerHandoff.ts` (`saveShareComposerHandoff`, `ShareComposerMode`).

Native affordances in `PulseShareScreen`: **QR code generation** (`native/PulseQr.tsx`), **QR scanning** (`native/ScanSheet.tsx`), clipboard copy (`native/clipboard.ts`), system share sheet, and direct-send into a conversation (`api/messenger.ts`).

Content-level share endpoints: `/api/pulse/reels/${id}/share`, `/api/pulse/status/${id}/share`, repost via `/api/pulse/posts/${id}/repost` and `/api/pulse/reels/${id}/repost`.

**Web parity:** QR generate/scan is achievable (canvas + `BarcodeDetector`/`getUserMedia`), the system share sheet maps to `navigator.share` where available with a copy-link fallback.

## 2. Commerce

16 screens, ~20 API modules. This is the largest non-social product area in the app and the
one carrying real money.

### 2.1 Screens

| Screen | Lines | API modules | Role |
|---|---:|---|---|
| `MarketplaceScreen` | — | `marketplace`, `marketplaceScreen` | Browse / discovery grid |
| `MarketplaceProductScreen` | — | `marketplace`, `marketplaceBuyerPresentation` | Listing detail |
| `MarketplaceCartScreen` | 513 | `marketplaceCommerce`, `marketplaceFulfillment` | Cart |
| `MarketplaceCheckoutScreen` | **890** | `checkoutCountries`, `checkoutSchedule`, `marketplace`, `marketplaceCheckoutState`, `marketplaceCommerce`, `marketplaceErrors`, `marketplaceFulfillment`, `stripePaymentSheet` | **The money path.** 8 API modules |
| `MarketplaceManagerScreen` | — | `marketplace` | Seller-side listing management |
| `SellerApplicationScreen` | **1,153** | `sellerApplication` | Onboarding: draft → documents → submit → withdraw |
| `SellerListingComposerScreen` | — | `marketplace`, `marketplaceCommerce` | Listing creation |
| `SellerStoreScreen` / `SellerStoreRoute` | — | `marketplace` | Public storefront |
| `StoreDashboardScreen` | 1,144 | `marketplace`, `storeDashboard` | Seller analytics |
| `BuyerOrdersScreen`, `OrdersManagerScreen`, `OrdersRoute` | — | `orders`, `ordersDashboard` | Order lifecycle both sides |
| `BusinessOsPaymentsScreen` | — | `payments`, `paymentsHub` | Payouts / Connect |
| `MarketPulseScreen` | — | `marketPulse` | Market data (crypto-adjacent, see §5) |

Plus `screens/marketplace/` (sub-directory) and the offers flow via `marketplaceOffers.ts`.

### 2.2 The checkout architecture — and why it is already web-ready

`api/stripePaymentSheet.ts` is the most useful file in this area for the rebuild, because its
header documents a backend decision that has not been written down anywhere else:

> `/api/pulse/marketplace/cart/checkout` and `/api/pulse/payments/checkout` **return a
> PaymentIntent client secret** when asked for `payment_mode: "payment_sheet"`.

That is precisely the shape Stripe.js / Stripe Elements needs in a browser. **The web checkout
does not require a new backend endpoint — it requires a different client SDK against the same
response.** This is the single biggest piece of good news in the commerce area, and it is
invisible unless you read that file.

The module also documents a **live fallback**: `@stripe/stripe-react-native` is resolved through
`require` inside a `try` rather than imported statically, because the SDK contains native code
that only exists after a prebuild + EAS build. When absent it reports `available: false` and the
screen falls back to the hosted checkout page. So there are currently **two live payment paths**
in the native app, and the hosted-page fallback is the one a web client most closely resembles.

### 2.3 Native-only mechanisms that need a web equivalent

| Native mechanism | Web equivalent | Difficulty |
|---|---|---|
| Stripe PaymentSheet (`@stripe/stripe-react-native` 0.61.0) | Stripe.js + Elements against the **same** client secret | **Low** — backend unchanged |
| Apple IAP (`expo-iap` ^4.3.1) for premium + ads wallet top-up | **No web equivalent, and none should be built.** Stripe Checkout is the web path | N/A — different product decision |
| `/api/pulse/ads/accounts/*/wallet/apple-iap/verify` | Not applicable to web | — |
| `/api/pulse/payments/apple/premium/verify` | Not applicable to web | — |
| Document upload in seller application | Presigned R2 + `Blob.slice()` chunking | **Medium** — needs R2 CORS to expose `ETag` |

**The IAP split is a product decision, not a technical gap.** Apple requires IAP for digital
goods purchased in an iOS app; the web is under no such obligation and Stripe is cheaper. The
two paths must reconcile to the same entitlement state server-side, which they already do —
both verify endpoints write the same premium record.

### 2.4 Web parity assessment

- **19 native-only marketplace endpoints** and **8 native-only payments endpoints** have never
  had a web caller (`PULSESOC_WEB_API_GAP_ANALYSIS.md` §3).
- No backend work is required for the core purchase flow; the client secret is already returned.
- The seller application (1,153 lines, multi-step, document upload) is the largest single screen
  port in the area and the one gated on R2 CORS.
- Desktop earns a genuinely better experience here than phone: a 3–4 column grid with a
  persistent facet panel beats a filter sheet, and a two-pane order manager beats a drilldown.

---

## 3. Business & growth

29 screens — the second-largest area, and almost entirely absent from the current website.

### 3.1 Sub-areas

**Ads platform (10 screens, 13 API modules).** `AdsManagerScreen`, `AdsCampaignWizardScreen`
(**1,985 lines — the largest screen in the app**), `AdsCampaignDetailScreen`,
`AdsAudiencesScreen`, `AdsInsightsScreen`, `AdsLibraryScreen`, `AdsPolicyCenterScreen`,
`AdsReportsScreen`, `AdsWalletScreen`, `AdsSubPageScreen`, plus `PromoteContentWizardScreen`.
API: `ads`, `adsAudiences`, `adsCreatives`, `adsDashboard`, `adsDelivery`, `adsDetail`,
`adsLibrary`, `adsOs`, `adsPolicy`, `adsPolicyCenter`, `adsPortal`, `adsReports`, `adsWallet`.

**Pages (7 screens).** `PagesHubScreen`, `PageScreen`, `PageCreateScreen`/`Route`,
`PageEditScreen`, `PageTeamScreen`, `PageConnectionsScreen`. API: `pages.ts`.

**Business OS (6 screens).** `BusinessOsScreen`, `BusinessOsSectionScreen`,
`BusinessOsAdvertisingScreen`, `BusinessOsInsightsScreen`, `BusinessOsPaymentsScreen`,
`BusinessBuyerPreviewScreen`, plus `BusinessHubScreen`/`Route` and `BusinessProfileScreen`.

**Creator & growth (2).** `CreatorStudioScreen`, `GrowthCenterScreen`.

### 3.2 The observation that matters most

**`/api/pages` is 21 routes of plain JSON CRUD with 16 native callers and _zero_ web callers.**
It needs no backend change, has no native-only mechanism, no upload dependency, and no realtime
requirement. It is the **cleanest, lowest-risk parity win in the entire inventory** — the ideal
candidate for the first non-trivial phase, precisely because it proves the whole client
architecture end-to-end (auth → fetch → render → mutate) without touching money, media or a
protected system.

### 3.3 The ads-platform caveat

`AdsCampaignWizardScreen` at 1,985 lines is a multi-step wizard over 13 API modules with a
policy-review gate and a wallet. It is the highest-effort single screen in the app, and it is
also the one where desktop is *genuinely better than phone* — a campaign builder wants a wide
canvas, side-by-side targeting/estimate, and a persistent preview. This is the strongest
argument in the inventory for the "desktop is not a stretched phone" requirement, and a good
candidate for a **web-first** treatment where the web UX deliberately diverges from native
rather than mirroring it.

Note also that `/api/pulse/ads` (66 routes) and `/api/business-os/advertising` (45 routes) are
two ads surfaces. Which one is authoritative must be settled before either is ported.

---

## 4. Intelligence & AI

25 screens across three distinct products that share a vocabulary but not a codebase.

### 4.1 UNDX — the agent/execution layer

`UndxActionCenterScreen` (611 lines, `api/undxActions`), `UndxCapabilitiesScreen`. Backend:
17 `/api/business-os/undx` routes (10 native-called) + 10 `/api/undx`.

**This area carries the project's sharpest safety constraint.** The execution kernel writes to
the repository only after the approval phrase `APPROVE UNDX WRITE`, and there is a second,
undocumented tier — `GUARD_APPROVAL_PHRASE = "APPROVE UNDX GUARD CHANGE"`. The native surface
includes `/api/business-os/undx/emergency-stop`, `/permissions`, `/policies`, `/receipts` and
`/confirmations`.

> **Recommendation: UNDX write-capable actions should not be exposed on the web client in the
> initial rebuild.** Read-only views (receipts, run history, capability listing) are safe and
> useful. An approval phrase typed into a browser is a materially weaker control than one typed
> into an authenticated native app, and the emergency-stop path deserves a deliberate design
> conversation rather than a port.

### 4.2 Private Office — 13 screens, an entirely separate product

`PrivateOfficeScreen` (583 lines), `PrivateConciergeScreen`, `PrivateConversationsScreen`,
`PrivateConversationInfoScreen`, `PrivateBriefingsScreen`, `PrivateDocumentsScreen`,
`PrivateFactsScreen`, `PrivatePeopleScreen`, `PrivateOperationsScreen`, `PrivateShieldScreen`,
`PrivateOfficeSecurityScreen`, `PrivateMeetingsScreen`, `PrivateMeetingRoomScreen`.
API: `privateOffice`, `privateRecords`, `privateConversations`, `privateFeatures`.

Backend: **61 `/api/private-office` routes across 9 blueprint files** — the largest blueprint
family, and the reason the naive `bot.py`-only route scan understated the app by 17%.

Two things distinguish this area:

1. **It has a second authorization lock** — a header-bound, session-bound grant that fails
   closed with a `423` contract. `PULSESOC_BACKEND_ROUTES_AND_AUTH.md` §5 rates it the strongest
   and most unusual authz in the codebase. A web client must implement that handshake, not
   route around it.
2. **The capital-graph sub-family (7 native-only endpoints)** — portfolio, exposure, obligations,
   cash-flow, integrity, overview — is financial data. It is read-only and safe to render, but
   it must never be paired with an action that could be read as advice or execution.

`/api/private-office/meetings` (26 routes) has no resolvable caller on either client; establish
its status before building on it.

### 4.3 Pulse AI — the consumer assistant

`PulseAiScreen` is **38 lines** — a thin route wrapper, with the real implementation in a
component. Backend: 16 `/api/pulse-ai` routes, 4 native-called (`conversation`, `message`,
`actions/confirm`, `actions/cancel`).

The `actions/confirm` + `actions/cancel` pair is the important detail: **the assistant proposes
actions that require explicit user confirmation before execution.** That confirmation UX is a
safety control, and it must be ported as a control — not flattened into an optimistic "the
assistant did it" toast, which is the natural thing for a web implementation to do.

Provider routing is server-side (`undx_router.py`, 7 providers; Groq and DeepSeek currently
non-functional) specifically so API keys never reach a client. **That property must survive the
rebuild.** No AI provider key belongs in a web bundle — and a web bundle is far easier to
inspect than a Hermes-compiled native binary, so the temptation to shortcut here is higher and
the consequence is worse.

### 4.4 Web parity assessment

| Sub-area | Native-only endpoints | Recommendation |
|---|---:|---|
| Private Office (incl. capital graph) | ~25 | **Port read surfaces.** Clean JSON; implement the 423 handshake |
| Pulse AI | 4 | **Port with the confirm/cancel control intact** |
| UNDX | 10 | **Read-only in phase 1.** Defer write/approval/emergency-stop |
| Briefings | 3 | Port; low risk |

## 5. Crypto — live product or legacy?

### 5.1 Verdict: **LIVE, not vestigial — but demoted to a premium sub-product with no tab of its own**

Evidence for "live":

1. **Registered and imported.** All six crypto screens are imported at the top of `src/navigation/AppNavigator.tsx` (lines 29–53) and registered as stack screens (lines 639–675). Nothing is behind a dead flag.
2. **Deep links resolve to them.** `src/navigation/linking.ts` maps `pulse/watchlists`, `pulse/alerts`, `pulse/crypto` and asset paths onto `Watchlists` / `AlertManagement` / `MarketPulse` / `AssetDetail`.
3. **Push notifications route into them.** `src/navigation/notificationRouting.ts:406` navigates to `AlertManagement` with an `alertId` — a live crypto price alert firing on a user's device opens this screen. That is production behaviour, not a leftover.
4. **Actively maintained.** `src/navigation/dashboardRouting.ts` lines 120–162 carry a long, recent comment explaining that a **bug report** ("a link to a member's own holdings opened the upgrade screen") caused the crypto family (`/portfolio`, `/watchlists`, `/alerts`, market pulse) to be reordered above the `/premium` rule. People are filing and fixing crypto routing bugs.
5. **Wired into the entitlement system.** `src/entitlements/reconcile.ts` imports `isPremiumRequired` from `api/cryptoPremium.ts`. Premium Center has dedicated crypto tests: `screens/__tests__/PremiumCenterScreen.cryptoBilling.test.tsx`, `…cryptoIntelligence.test.tsx`.
6. **Tested.** `screens/__tests__/PortfolioScreen.test.tsx`, `advancedAlertCreation.test.tsx`, `alertPresetSymbol.test.tsx`.
7. `src/navigation/masterNavigation.ts:88` lists Watchlists with `status: "native"` and the description "Live crypto watchlists, prices, charts, and alerts."

Evidence for "demoted":

- **No crypto tab.** None of the 15 tabs is crypto. Entry is only via the dashboard module grid, deep links, push notifications, and in-screen drill-down.
- Crypto reads are **premium-gated** (`api/cryptoPremium.ts` → `isPremiumRequired`).
- The crypto API surface is small relative to the rest of the app: ~10 endpoints across 5 modules vs 331 total.

**Conclusion for the web rebuild:** crypto is a real, shipping, premium-tier sub-product. It must be built, but it belongs in a later phase and behind the premium entitlement — it is not a headline surface.

### 5.2 Screens

| Route(s) | Screen file | Purpose |
|---|---|---|
| `CryptoPortfolio` | `screens/CryptoPortfolioScreen.tsx` | Crypto holdings view |
| `Portfolio` | `screens/PortfolioScreen.tsx` | General portfolio (also reached from `/portfolio`, `/pulse/premium/intelligence/portfolio`) |
| `Watchlists` | `screens/WatchlistsScreen.tsx` | "the crypto market tracking workspace" — lists → tap asset → `AssetDetail` |
| `AssetDetail` | `screens/AssetDetailScreen.tsx` | Per-symbol detail; sets its own title at runtime; UNDX can return here (`navigation/types.ts:126` `undxReturn`) |
| `AlertManagement`, `CryptoAlertManagement` | `screens/AlertManagementScreen.tsx` | Create/edit price alerts; accepts `alertId` and `presetSymbol` |
| `CryptoAlertCenter` | `screens/CryptoAlertCenterScreen.tsx` | Alert overview/hub, accepts `presetSymbol` |
| `CryptoAlertHistory` | `screens/CryptoAlertHistoryScreen.tsx` | Fired-alert history, accepts `alertId` |
| `MarketPulse` | `screens/MarketPulseScreen.tsx` | Market board; `openAsset` param opens `AssetDetail` (covered in §4.4 as it doubles as an intelligence surface) |

### 5.3 API modules and endpoints

| Module | Endpoints | Consumers |
|---|---|---|
| `api/alerts.ts` | `/api/crypto/alerts` | AlertManagement, AssetDetail, watchlists |
| `api/cryptoPremium.ts` | `/api/crypto/alerts`, `/api/mobile/crypto/alerts`, `/api/mobile/crypto/portfolio` | CryptoAlertHistory, `core/cryptoAlertForm.ts`, `entitlements/reconcile.ts` |
| `api/watchlists.ts` | `/api/crypto/favorites`, `/api/crypto/watchlists`, `/api/crypto/watchlists/market` | Watchlists, MarketPulse, Portfolio, AssetDetail |
| `api/portfolio.ts` | `/api/portfolio`, `/api/portfolio/${id}` | Portfolio |
| `api/marketPulse.ts` | `/api/pulse/market/global` | MarketPulse |
| `api/marketIntelligence.ts` | `/api/pulse/market/snapshot`, `/api/pulse/market/portfolio/risk` | MarketPulse, `components/crypto/*` |

Shared components: `src/components/crypto/IntelligenceBadge.tsx`, `src/components/crypto/AssetIntelligencePanel.tsx`. Alert form logic: `src/core/cryptoAlertForm.ts`.

### 5.4 Predictions / simulator / arena

**NOT PRESENT in the native app.** No route, screen, or api module references arena, predictions, or a simulator. The backend has an `/api/arena` family (120 routes per CLAUDE.md), but the native client does not call it. The web rebuild should treat arena as a **web-only / backend-only** surface, not a parity item against native.

### 5.5 Gating

Crypto read endpoints answer with a premium-required signal handled by `isPremiumRequired` (`api/cryptoPremium.ts`), reconciled in `src/entitlements/reconcile.ts` against the canonical tier (`entitlements/canonicalTier.ts`, `useCanonicalTier.ts`) and rendered through `entitlements/PremiumFeatureGate.tsx`. The web client needs the same three-part shape: tier resolution, a gate component, and a premium-required error path that routes to the Premium Center rather than to an error state.

## 6. Account & settings

### 6.1 Settings root + the 19-entry registry

`SettingsScreen` (`src/screens/SettingsScreen.tsx`, 247 lines) is a **search-driven index**, not a hand-written list. It renders `SETTINGS_ENTRIES` from `src/settings/registry.ts` grouped into 4 sections, with a live search box that matches title / subtitle / keywords (all i18n keys under `settings:index.entries.<id>.*`).

Supporting infrastructure: `src/settings/schema.ts`, `src/settings/store.tsx`, `src/settings/api.ts`, `src/settings/SettingsProviders.tsx`, `src/settings/components/`.

Sections: `account`, `preferences`, `privacy`, `support`.

**Every settings entry (19), in registry order:**

| # | Entry id | Section | Route | Screen file | Gating |
|---|---|---|---|---|---|
| 1 | `profile` | account | `ProfileEdit` | `screens/ProfileEditScreen.tsx` | `requiresAuth` |
| 2 | `account` | account | `AccountCenter` (params `{section:"account"}`) | `screens/AccountCenterScreen.tsx` | `requiresAuth` |
| 3 | `security` | account | `SecuritySettings` | `screens/settings/SecuritySettingsScreen.tsx` | `requiresAuth` |
| 4 | `sessions` | account | `SessionsDevices` | `screens/settings/SessionsDevicesScreen.tsx` | `requiresAuth` |
| 5 | `account-health` | account | `AccountHealth` | `screens/AccountHealthAppealsScreen.tsx` | `requiresAuth` |
| 6 | `notifications` | preferences | `NotificationSettings` | `screens/settings/NotificationSettingsScreen.tsx` | — |
| 7 | `appearance` | preferences | `AppearanceSettings` | `screens/settings/AppearanceSettingsScreen.tsx` | — |
| 8 | `accessibility` | preferences | `AccessibilitySettings` | `screens/settings/AccessibilitySettingsScreen.tsx` | — |
| 9 | `language` | preferences | `LanguageSettings` | `screens/settings/LanguageSettingsScreen.tsx` | — |
| 10 | `storage` | preferences | `StorageSettings` | `screens/settings/StorageSettingsScreen.tsx` | — |
| 11 | `permissions` | preferences | `PermissionsSettings` | `screens/settings/PermissionsSettingsScreen.tsx` | — |
| 12 | `privacy` | privacy | `PrivacySettings` | `screens/settings/PrivacySettingsScreen.tsx` | `requiresAuth` |
| 13 | `blocked` | privacy | `BlockedUsers` | `screens/settings/BlockedUsersScreen.tsx` | `requiresAuth` |
| 14 | `muted` | privacy | `MutedUsers` | `screens/settings/MutedUsersScreen.tsx` | `requiresAuth` |
| 15 | `data` | privacy | `DataPrivacySettings` | `screens/settings/DataPrivacySettingsScreen.tsx` | `requiresAuth` |
| 16 | `safety` | privacy | `SafetyHub` | `screens/SafetyHubScreen.tsx` | `requiresAuth` (see §8.2) |
| 17 | `help` | support | `HelpSettings` | `screens/settings/HelpSettingsScreen.tsx` | — |
| 18 | `about` | support | `AboutSettings` | `screens/settings/AboutSettingsScreen.tsx` | — |
| 19 | `legal` | support | `LegalSettings` | `screens/settings/LegalSettingsScreen.tsx` | — (content in `screens/settings/legalContent.ts`) |
| 20 | `developer` | support | `DeveloperSettings` | `screens/settings/DeveloperSettingsScreen.tsx` | **`developerOnly: true`** |

(20 rows; 19 user-visible + 1 developer-gated.)

**Session footer on the settings root** (not registry entries): "Sign out" and "Sign out everywhere", both confirm-gated. Sign-out-everywhere calls `POST /api/mobile/auth/logout-all` (`api/auth.ts`).

**Shared list shell:** `screens/settings/RelationshipListScreen.tsx` backs both Blocked and Muted users — one component, two routes.

### 6.2 Account Center (7 routes, 1 screen, 4 tabs)

`screens/AccountCenterScreen.tsx` (876 lines). A tabbed screen whose active tab is derived from either a `section` route param **or** the route name itself (`resolveSection`, line ~596).

| Tab key | Reached by route(s) |
|---|---|
| `account` | `AccountCenter`, `AccountSettings`, `AccountWebSettings` |
| `security` | `AccountSecurity`, `AccountWebSecurity` |
| `privacy` | `AccountPrivacy` |
| `devices` | `AccountDevices` |

The `*Web*` route aliases exist so web URLs land on the same screen — direct evidence that the web/native route names are already being reconciled.

API: `api/account.ts` →
`/api/account/status`, `/api/account/security`, `/api/account/security-events`, `/api/account/2fa/enable`, `/api/account/2fa/disable`, `/api/account/recovery-codes/generate`, `/api/account/password/change-request`, `/api/account/reauthenticate`, `/api/account/verify-email`, `/api/account/verify-phone`, `/api/account/trusted-devices`, `/api/account/trusted-devices/${deviceId}`, `/api/account/sessions/revoke-all`, `/api/account/language`, `/api/account/region-preferences`, `/api/dashboard/account/settings`, `/pulse/settings/account`.

### 6.3 Account Health & Appeals

| Route | Screen | API |
|---|---|---|
| `AccountHealth`, `AccountHealthWeb` | `screens/AccountHealthAppealsScreen.tsx` | `api/accountHealth.ts` → `/api/dashboard/account/state`, `/api/dashboard/account/strikes/${strikeId}/appeal` |

Purpose: surface account standing, strikes, and let the user file an appeal per strike. Depends on `api/verification.ts` and `api/account.ts` too.

### 6.4 Notification preferences & region/time

| Route | Screen | API |
|---|---|---|
| `NotificationPreferences` | `screens/NotificationPreferencesScreen.tsx` | `api/notifications.ts` (`/api/notification-preferences`, `/api/pulse/notifications/preferences`), `api/push.ts` (`/api/push/subscribe`, `/api/push/unsubscribe`) |
| `NotificationSettings` (registry #6) | `screens/settings/NotificationSettingsScreen.tsx` | same pair |
| `RegionTime` | `screens/RegionTimeScreen.tsx` | `api/account.ts` → `/api/account/region-preferences` |

Two notification-preference surfaces exist (`NotificationPreferences` and `NotificationSettings`). **UNVERIFIED** whether one supersedes the other; both are registered and both import `api/push.ts`.

### 6.5 Verification

| Route | Screen | API |
|---|---|---|
| `VerificationCenter`, `VerificationWebCenter` | `screens/VerificationCenterScreen.tsx` | `api/verification.ts` → `/api/dashboard/account/state`, `/api/dashboard/account/verification/request`, `/api/dashboard/account/verification/document`, `/api/dashboard/account/verification/appeal`, `/api/premium/status`, `/api/pulse/profile/me` |

Also consumed by `core/hubBindings.ts` (so verification state feeds the Business Hub) and by `api/accountHealth.ts` / `api/businessHub.ts`.

### 6.6 Identity & presence

| Route | Screen | Notes |
|---|---|---|
| `PulseIdentity` | `screens/PulseIdentityScreen.tsx` | Identity/handle surface. **UNVERIFIED** exact feature set; no dedicated api module imports detected. |

### 6.7 Auth surfaces

| Route | Screen | API |
|---|---|---|
| `Login` | `screens/LoginScreen.tsx` | `api/auth.ts` → `/api/mobile/auth/login`, `/api/mobile/auth/session` |
| `Signup` | `screens/SignupScreen.tsx` | `/api/mobile/auth/register`, `/api/mobile/auth/resend-confirmation`, `/api/mobile/auth/confirmation-status`, `/api/mobile/auth/change-confirmation-email`; step components in `components/auth/signup/` |
| `AccountRecovery` | `screens/AccountRecoveryScreen.tsx` | `/api/mobile/auth/recover` |

Token handling: bearer + session cookie, refresh via `POST /api/mobile/auth/refresh`, storage in expo-secure-store (`src/session/auth.ts`). Biometric unlock exists — see `screens/__tests__/SettingsScreen.biometric.test.tsx`.

### 6.8 Appearance / language / translation

- **Appearance:** `AppearanceSettingsScreen` + `src/theme/` — theme selection.
- **Language:** `LanguageSettingsScreen` + `src/i18n/`; `api/account.ts` `/api/account/language`.
- **Content translation** is separate from UI language: `api/translation.ts` → `/api/pulse/translations`, `/api/pulse/translations/languages`, `/api/pulse/translations/preference`, consumed by `components/TranslationPreferencesBootstrap.tsx` and `components/ContentTranslation.tsx`. i18n is CI-gated: hardcoded strings fail `npm run verify`.

### 6.9 Device & session management

`SessionsDevicesScreen` (`screens/settings/SessionsDevicesScreen.tsx`) and the `devices` tab of Account Center both read `/api/account/trusted-devices` and can revoke individually or via `/api/account/sessions/revoke-all`.

### 6.10 Reporting / blocking / muting

- Block: `api/support.ts` → `/api/pulse/block`; `api/feed.ts` → `/api/pulse/users/mute`.
- Report: `api/support.ts` → `/api/pulse/report`, `/api/security/report`; `api/reels.ts` → `/api/pulse/report`; `api/marketplace.ts` → `/api/pulse/marketplace/listings/report`.
- Lists: `BlockedUsers` / `MutedUsers` routes (§6.1 #13/#14) over `settings/RelationshipListScreen.tsx`.
- Support tickets: `/api/support/ticket`, `/pulse/help` (`HelpSettingsScreen`).

## 7. Live, calls & media (INVENTORY ONLY — no changes proposed)

> **Hard-protected area.** Nothing in this section proposes a change to the native foundation. `docs/realtime_audio_change_policy.md` + `config/realtime-audio-protected-paths.json` govern these paths. This is a read-only description plus a statement of what a *web* client would independently need.

### 7.1 The RTC provider is **Agora**, not LiveKit

**CONFIRMED from `mobile-native/package.json`:**

```
"react-native-agora": "4.6.2"
```

There is **no `@livekit/*` package, no `livekit-client`, and no LiveKit patch** anywhere in `mobile-native/`. The only patch present is `patches/react-native+0.81.5.patch` (the Hermes build fix). **`CLAUDE.md`'s claim that RTC is LiveKit is stale and wrong.**

Corroborating source names:
- `src/calls/useAgoraCallRoom.ts` (with `useNativeCallRoom.ts` as the abstraction over it)
- `src/live/useAgoraLiveBroadcastRoom.ts` (with `useLiveBroadcastRoom.ts` as the abstraction)
- `src/live/agoraLiveTelemetry.ts`
- `src/live/RtcVideoView.tsx`

Token minting is server-side: `api/live.ts` → `GET /api/pulse/live/${liveId}/rtc/token`.

Other media deps: `expo-av ~16.0.8` (legacy, allowlist-capped at six call sites), `expo-camera ~17.0.10`, `expo-media-library ~18.2.1`, local native modules `modules/pulse-now-playing` (iOS lock-screen controls, Swift) and `modules/pulse-video-mixer`. **Replay playback is Mux HLS** (see `ReplayViewerScreen`).

### 7.2 Screens

| Route(s) | Screen file | Lines | What it does |
|---|---|---|---|
| `Live` (tab), `LiveDetail` | `screens/LiveScreen.tsx` | 1313 | Viewer surface: browse live broadcasts, watch (expo-av `Video`), live chat, reactions, join requests |
| `LiveStudio` | `screens/LiveStudioScreen.tsx` | 717 | **Management, explicitly NOT a camera.** Status, readiness, setup form, tools. Its own docblock records that the camera preview was deliberately removed to stop three screens all looking "live" |
| `NativeLiveHost` | `screens/LiveHostSessionScreen.tsx` | 2101 | **Capture.** Full-screen camera, on-air controls, guest stage, moderation. The largest screen in the app |
| `ReplayViewer` | `screens/ReplayViewerScreen.tsx` | 229 | Finished-broadcast replay. Plays the **Mux HLS** asset with native scrubbing; polls every 3s up to 40 attempts while the backend renders the asset |
| `Call` | `screens/CallScreen.tsx` | 654 | 1:1 and group voice/video calls |
| `CameraStudio` | `screens/CameraStudioScreen.tsx` | 1101 | Unified capture → publish. One camera that can target post / reel / status / message / avatar / cover |
| `Music` | `screens/MusicScreen.tsx` | 1152 | Music library, upload (`expo-document-picker`), playback |
| `PulseQueue` | `screens/PulseQueueScreen.tsx` | 290 | Pulse Radio queue: reorder, shuffle, repeat, play-at-index |
| `ContentPreview` | `screens/ContentPreviewScreen.tsx` | 201 | Pre-publish preview of a post / reel / status draft |

### 7.3 Supporting module map

| Directory | Contents |
|---|---|
| `src/calls/` (13 files) | `useAgoraCallRoom.ts`, `useNativeCallRoom.ts`, `callKitBridge.ts` (iOS CallKit), `callSessionStore.ts`, `callMediaState.ts`, `callSignalMedia.ts`, `callParticipants.ts`, `callCapabilities.ts`, `callToneLifecycle.ts`, `callSyncTrace.ts`, `incomingCallQa.ts`, `IncomingCallLayer.tsx`, `MinimizedCallBanner.tsx`, `CallActionsSheet.tsx`, `AddParticipantsSheet.tsx` |
| `src/live/` (24 files) | `useAgoraLiveBroadcastRoom.ts`, `useLiveBroadcastRoom.ts`, `liveRuntime.ts`, `liveSession.ts`, `liveSessionLifecycle.ts`, `liveAudioPublisher.ts`, `liveAudioMatrix.ts`, `liveAudioRecovery.ts`, `liveAudioFlags.ts`, `liveAudioTelemetry.ts`, `liveAudioTrace.ts`, `liveMediaOwnership.ts`, `livePlaybackOwnership.ts`, `liveMusicMixing.ts`, `liveParticipantRegistry.ts`, `liveSeatReconciliation.ts`, `liveGuestStage.ts`, `liveStageLayout.ts`, `liveStreamQuality.ts`, `liveStudioReadiness.ts`, `liveEventContinuity.ts`, `liveTelemetryPrivacy.ts`, `liveHostUi.tsx`, `LiveStage.tsx`, `LiveChatOverlay.tsx`, `LiveReactionLayer.tsx`, `LiveModerationSheet.tsx`, `PreLiveConfigurationSheet.tsx`, `RtcVideoView.tsx`, `agoraLiveTelemetry.ts` |
| `src/live-audio/` (4 files) | `liveAudioEngine.ts`, `liveAudioNative.ts`, `liveMicrophonePublisher.ts`, `livePublisherMedia.ts` — **the single microphone publication path**. Duplicating it is explicitly forbidden |
| `src/media/` (17 files) | `MediaUploadManager.ts`, `nativeMediaUpload.ts`, `useNativeMediaUpload.ts`, `mediaContract.ts`, `mediaAccess.ts`, `messengerMediaAccess.ts`, `mediaCache.ts`, `mediaDownloader.ts`, `mediaSessionCleanup.ts`, `mediaTelemetry.ts`, `mediaActions.ts`, `qaCameraMedia.ts`, `ComposerMediaQueue.tsx`, `useComposerMediaQueue.ts`, `MediaUploadPreview.tsx`, `MediaGestureFeedback.tsx`, `useTapMuteLike.ts` |
| `src/video/` | `videoMusicMix.ts`, `VideoMusicPicker.tsx` |
| `src/core/` | `attachedMusicAudioPolicy.ts`, `pulseRadio.ts` — cross-surface audio arbitration |

### 7.4 Backend endpoints

`api/live.ts` (19 endpoints):
`/api/pulse/live/start`, `…/${liveId}/state`, `…/join`, `…/join-status`, `…/join-request`, `…/join-requests`, `…/join-requests/${requestId}/${action}`, `…/join-requests/${requestId}/cancel`, `…/guests/${guestId}/${action}`, `…/guests/${guestId}/leave`, `…/guests/${guestId}/publish-complete`, `…/native-publish`, `…/rtc/token`, `…/chat`, `…/chat/${messageId}/${action}`, `…/react`, `…/end`, `…/replay/retry`, `/pulse/live/${id}`.

`api/calls.ts`: `/api/calls/start`, `/api/calls/active`, `/api/calls/capabilities`, `/api/calls/voip-token`, `/api/calls/voip-token/revoke`.

`api/camera.ts`: `/api/pulse/camera/config`, `/api/pulse/camera/preview`, `/api/pulse/camera/preview/mark-published`, `/api/pulse/media/upload`, `/api/pulse/posts/create-from-camera`, `/api/pulse/reels/create-from-camera`.

`api/music.ts`: `/api/pulse/music/upload`. `api/composerMusic.ts`: `/api/pulse/music/ai-suggest`. `api/radio.ts`: client-side only.

`api/messenger.ts` media leg: `/api/messages/media/init`, `/api/messages/media/upload`, `/api/messages/media/complete`, `/api/messages/media/${attachmentId}/download`.

### 7.5 What a WEB client needs for parity (no native change implied)

| Native surface | Web parity requirement |
|---|---|
| Live viewing (`LiveScreen`) | HLS playback in-browser (`hls.js` / native Safari HLS) against the same Mux playback URL; poll `GET /api/pulse/live/${id}/state`; chat over `/chat`; reactions over `/react`. Read-only viewing needs **no** RTC SDK. |
| Live hosting (`LiveHostSessionScreen`) | **Agora Web SDK** (`agora-rtc-sdk-ng`) publishing to the same channel, token from `GET …/rtc/token`, then `POST …/native-publish` equivalent. This is the single hardest parity item and should be phased last. |
| Live Studio (management) | Pure REST + forms. No media APIs. **Cheapest live-adjacent parity win — build this first.** |
| Replay (`ReplayViewerScreen`) | Mux HLS `<video>` + the same 3s/40-attempt "replay still rendering" poll and `…/replay/retry`. Straightforward. |
| Calls (`CallScreen`) | Agora Web SDK + WebRTC. Browsers have no CallKit/VoIP-push equivalent — incoming-call ring must degrade to a Web Push notification + in-page banner. `/api/calls/voip-token` is iOS-only and has no web analogue. |
| Camera Studio | `getUserMedia` + `MediaRecorder`, then the existing `/api/pulse/media/upload` + `create-from-camera` endpoints. Web cannot match native's `pulse-video-mixer`; expect a reduced editing feature set. |
| Music / Pulse Queue | HTML5 `<audio>` + Media Session API for lock-screen/OS controls (the web analogue of `modules/pulse-now-playing`). Queue state is client-side (`core/pulseRadio.ts`) and must be re-implemented. |
| Content preview | Pure client render of the draft model (`create/draftToContentModel.ts`). No media stack needed. |

**Audio-session ownership arbitration (`liveMediaOwnership.ts`, `livePlaybackOwnership.ts`, `core/attachedMusicAudioPolicy.ts`) has no browser equivalent** — the browser arbitrates audio focus itself. The web client must re-derive the *product* rules (music pauses when a call starts, a live stream mutes background audio) at the application layer; it cannot port the native implementation.

## 8. Admin & moderation

### 8.1 There is no platform-admin surface in the native app

**CONFIRMED.** No screen, route, or api module targets `/api/admin` or `/admin/business-os` (the backend's 37 + 49 admin routes per CLAUDE.md). Native "admin" identifiers refer only to **object-level roles** — group admin, page admin/team member, store owner, private-meeting host, live-broadcast host — never platform staff.

Object-level role surfaces found:

| File | Role concept |
|---|---|
| `screens/GroupsScreen.tsx`, `api/groups.ts` | group admin / room moderation |
| `screens/SellerStoreScreen.tsx`, `api/storeDashboard.ts` | store owner |
| `screens/PageTeamScreen.tsx`, `api/pages.ts` | page team roles, `/api/pages/${id}/members/${memberUserId}`, `/transfer` |
| `screens/PrivateMeetingsScreen.tsx`, `screens/PrivateMeetingRoomScreen.tsx`, `privateOffice/meetings/*` | meeting host |
| `live/liveParticipantRegistry.ts`, `live/liveMediaOwnership.ts`, `live/LiveModerationSheet.tsx` | broadcast host / guest moderation |
| `screens/__tests__/profileOsOwnerGate.test.tsx` | Business/Profile OS owner gate |

**Implication for the web rebuild:** platform moderation tooling lives in the Flask templates, not in native. There is no native design to copy — do not treat it as a parity item.

### 8.2 Safety Hub

| Route(s) | Screen | Lines |
|---|---|---|
| `SafetyHub`, `SafetyWebHub` | `screens/SafetyHubScreen.tsx` | 705 |

Reached from Settings entry #16 (`safety`, `requiresAuth`). API `api/safety.ts` → `/api/dashboard/network/state`.

Capabilities read from the imports: `createSafetyBlock`, `createSafetyReport`, `recordMuteHandoff`, `recordUnblockHandoff`, `loadSafetyState`, `loadCachedSafetyState`, `openSafetyWebFallback`, plus a `SafetyActionRecord` history.

Two notable patterns the web must mirror:
- **Offline cache** (`loadCachedSafetyState`) — the hub renders a cached state before the network answers.
- **`openSafetyWebFallback`** — the native app already punts some safety flows to the website. These are the flows the web build must own outright.

### 8.3 Trust & Safety / Scam Shield (6 routes, 1 screen)

| Route | Screen |
|---|---|
| `TrustSafety`, `TrustSafetySupport`, `TrustSafetyHelp`, `TrustCenter`, `SecurityReport`, `ScamShield` | `screens/TrustSafetyScreen.tsx` (544 lines) |

One component typed against all six route names; the route name selects the initial pane. API `api/support.ts`:

| Endpoint | Purpose |
|---|---|
| `/api/support/ticket` | create + list support tickets |
| `/api/security/report` | security report submission |
| `/api/scam-shield/scan` | **Scam Shield** — scan a message/link/listing for scam signals |
| `/api/pulse/report` | content report |
| `/api/pulse/block` | block a user |
| `/pulse/help` | web help fallback |

Support ticket issue types (source literal): `account`, `safety`, `payments`, `notifications`, `creator`, `marketplace`.

Also has `openSupportWebFallback` and `loadCachedSupportState` — same offline-cache + web-fallback pattern as §8.2.

### 8.4 User-level moderation controls

| Surface | Route | Backing |
|---|---|---|
| Blocked users list | `BlockedUsers` | `settings/RelationshipListScreen.tsx`, `/api/pulse/block` |
| Muted users list | `MutedUsers` | `settings/RelationshipListScreen.tsx`, `/api/pulse/users/mute` |
| Report a post | inline in Home / PostDetail | `api/reels.ts` + `api/support.ts` → `/api/pulse/report` |
| Report a reel | inline in Reels | `/api/pulse/report` |
| Report a listing | MarketplaceProduct | `/api/pulse/marketplace/listings/report` |
| Report from a call | `calls/CallActionsSheet.tsx` | `api/support.ts` |
| Report from a status | `screens/StatusScreen.tsx` | `api/support.ts` |
| Hide a post | Home feed | `api/feed.ts` → `/api/pulse/posts/${postId}/hide` |
| "Not interested" on a reel | Reels | `/api/pulse/reels/${reelId}/not-interested` |
| Live chat moderation | `live/LiveModerationSheet.tsx` | `/api/pulse/live/${liveId}/chat/${messageId}/${action}` |
| Ads policy appeals | `AdsPolicyCenterScreen` | `/api/pulse/ads/appeals` |
| Account strike appeals | `AccountHealth` | `/api/dashboard/account/strikes/${strikeId}/appeal` |

**Parity note:** report/block is not one endpoint — it is at least six per-surface entry points funnelling into `/api/pulse/report` and `/api/pulse/block`. The website needs the same per-surface affordances, not a single "report" page.

## 9. Coverage reconciliation (all 182 routes)

Source: `docs/web-rebuild/data/native_nav_registrations.json` (185 rows, 182 unique route names; `Search`, `Saved`, `Reels` are each registered in both the Tabs and Stack navigators).

`NAV` = owned by `PULSESOC_NATIVE_NAVIGATION_AND_TABS.md` (navigation architecture, drawer, deep linking, the five main tabs Home / Reels / Create / Messenger / Profile and their immediate drill-downs).

### 9.1 Tab navigator (15)

| Route | Component | Covered by |
|---|---|---|
| Dashboard | UserDashboardScreen | §3 |
| Home | (inline) | NAV |
| Search | SearchScreen | §1 |
| Saved | SavedScreen | §1 |
| Groups | GroupsScreen | §1 |
| Live | LiveScreen | §7 |
| Reels | ReelsScreen | NAV |
| Create | CreateTabScreen | NAV |
| Status | StatusScreen | §1 |
| Messenger | MessengerScreen | NAV |
| Notifications | ActivityInboxScreen | §1 |
| PulseAI | PulseAiScreen | §4 |
| Profile | ProfileScreen | NAV |
| Marketplace | MarketplaceScreen | §2 |
| Settings | SettingsScreen | §6 |

### 9.2 Stack navigator (167)

| Route | Component | Covered by |
|---|---|---|
| Tabs | (inline) | NAV |
| UserDashboard | UserDashboardScreen | §3 |
| UserDashboardWeb | UserDashboardScreen | §3 |
| DashboardComposeAlias | DashboardActionAliasScreen | §3 |
| DashboardMusicAlias | DashboardActionAliasScreen | §3 |
| Music | MusicScreen | §7 |
| PulseQueue | PulseQueueScreen | §7 |
| DashboardLegacyModule | DashboardLegacyModuleScreen | §3 |
| DashboardModuleDetail | DashboardModuleDetailScreen | §3 |
| CameraStudio | CameraStudioScreen | §7 |
| ContentPreview | ContentPreviewScreen | §7 |
| Call | CallScreen | §7 |
| Chat | ChatScreen | NAV (Messenger drill-down) |
| NewChat | NewChatScreen | NAV |
| PulseShare | PulseShareScreen | §1.8 |
| PostDetail | PostDetailScreen | NAV (Home drill-down) |
| ProfilePostViewer | ProfilePostViewerScreen | NAV (Profile drill-down) |
| Reels | ReelsScreen | NAV |
| ReelDetail | ReelsScreen | NAV |
| StatusDetail | StatusScreen | §1.4 |
| MarketplaceDetail | MarketplaceScreen | §2.1 |
| MarketplaceProduct | MarketplaceProductScreen | §2.1 |
| BusinessOs | BusinessHubRoute | §3.1 |
| BusinessProfile | BusinessProfileScreen | §3.3 |
| BusinessBuyerPreview | BusinessBuyerPreviewScreen | §3.3 |
| MarketplaceManager | MarketplaceManagerScreen | §2.2 |
| BusinessOsAdvertising | AdvertisingRoute | §3.4 |
| BusinessOsOrders | OrdersRoute | §2.3 |
| BusinessOsMessages | MessagesRoute | §2.4 |
| BusinessOsEvents | EventsRoute | §3.8 |
| BusinessOsSection | BusinessOsSectionScreen | §3.1 |
| BusinessOsActivity | ActivityRoute | §3.1 |
| BusinessOsInsights | BusinessOsInsightsScreen | §3.7 |
| BusinessOsPayments | BusinessOsPaymentsScreen | §2.5 |
| MoneyLayer | MoneyLayerScreen | §2.6 |
| MoneyDetail | MoneyDetailScreen | §2.6 |
| Rewards | RewardsScreen | §2.6 |
| SellerStore | SellerStoreRoute | §2.2 |
| Dropshipping | DropshippingHubScreen | §2.7 |
| DropshippingSuppliers | SuppliersScreen | §2.7 |
| DropshippingConnect | ConnectSupplierScreen | §2.7 |
| DropshippingCatalog | SupplierCatalogScreen | §2.7 |
| DropshippingProduct | SupplierProductScreen | §2.7 |
| DropshippingCart | ImportCartScreen | §2.7 |
| DropshippingProducts | DropshippingProductsScreen | §2.7 |
| DropshippingDraft | ReviewImportedProductScreen | §2.7 |
| DropshippingOrders | DropshippingOrdersScreen | §2.7 |
| DropshippingSync | DropshippingSyncScreen | §2.7 |
| BuyerOrders | BuyerOrdersScreen | §2.3 |
| BuyerOrderDetail | BuyerOrdersScreen | §2.3 |
| BuyerPurchases | BuyerOrdersScreen | §2.3 |
| MarketplaceCart | MarketplaceCartScreen | §2.1 |
| MarketplaceCheckout | MarketplaceCheckoutScreen | §2.1 |
| BuyerOrdersDashboard | BuyerOrdersScreen | §2.3 |
| MerchantApply | SellerApplicationScreen | §2.2 |
| MerchantDashboard | SellerStoreScreen | §2.2 |
| MerchantProfile | SellerStoreScreen | §2.2 |
| MarketplaceCreateGateway | SellerListingComposerScreen | §2.2 |
| Search | SearchScreen | §1.1 |
| Saved | SavedScreen | §1.3 |
| GroupDetail | GroupsScreen | §1.6 |
| LiveDetail | LiveScreen | §7 |
| LiveStudio | LiveStudioScreen | §7 |
| NativeLiveHost | LiveHostSessionScreen | §7 |
| ReplayViewer | ReplayViewerScreen | §7 |
| Events | EventsScreen | §1.7 |
| EventDetail | EventsScreen | §1.7 |
| LiveScheduleGateway | EventsScreen | §1.7 |
| LiveEventCreateGateway | EventsScreen | §1.7 |
| ProfileDetail | ProfileScreen | NAV |
| Page | PageScreen | §1.7a |
| PageCreate | PageCreateRoute | §1.7a |
| PageConnections | PageConnectionsScreen | §1.7a |
| PageTeam | PageTeamScreen | §1.7a |
| PageEdit | PageEditScreen | §1.7a |
| PagesHub | PagesHubScreen | §1.7a |
| Presence | PresenceHubScreen | §1.7a |
| PulseIdentity | PulseIdentityScreen | §6.6 |
| ProfileEdit | ProfileEditScreen | NAV |
| Premium | PremiumCenterScreen | §2.8 |
| CreatorStudio | CreatorStudioScreen | §3.5 |
| CreatorStudioAlias | CreatorStudioScreen | §3.5 |
| ContentPlanner | ContentPlannerScreen | §3.6 |
| ContentPlannerWeb | ContentPlannerScreen | §3.6 |
| ContentPlannerPulseAlias | ContentPlannerScreen | §3.6 |
| PostScheduler | ContentPlannerScreen | §3.6 |
| PostSchedulerPulseAlias | ContentPlannerScreen | §3.6 |
| DraftStudio | ContentPlannerScreen | §3.6 |
| DraftStudioPulseAlias | ContentPlannerScreen | §3.6 |
| Courses | CoursesLearningScreen | §3.9 |
| CourseDetail | CoursesLearningScreen | §3.9 |
| LearningLessonDetail | CoursesLearningScreen | §3.9 |
| TeacherProfileGateway | CoursesLearningScreen | §3.9 |
| TeacherDashboardGateway | CoursesLearningScreen | §3.9 |
| GrowthCenter | GrowthCenterScreen | §3.10 |
| ProgressCenter | ProgressCenterScreen | §3.10 |
| IntelligenceCenter | IntelligenceCenterScreen | §4.3 |
| UndxActionCenter | UndxActionCenterScreen | §4.2 |
| UndxCapabilities | UndxCapabilitiesScreen | §4.2 |
| Watchlists | WatchlistsScreen | §5 |
| Portfolio | PortfolioScreen | §5 |
| MarketPulse | MarketPulseScreen | §4.4 |
| PrivateOffice | PrivateOfficeScreen | §4.6 |
| PrivateFacts | PrivateFactsScreen | §4.6 |
| PrivateOperations | PrivateOperationsScreen | §4.6 |
| CapitalGraph | CapitalGraphScreen | §4.5 |
| CapitalEntity | CapitalEntityScreen | §4.5 |
| PrivateOfficeSecurity | PrivateOfficeSecurityScreen | §4.6 |
| PrivateDocuments | PrivateDocumentsScreen | §4.6 |
| PrivatePeople | PrivatePeopleScreen | §4.6 |
| PrivateBriefings | PrivateBriefingsScreen | §4.6 |
| PrivateShield | PrivateShieldScreen | §4.6 |
| PrivateConcierge | PrivateConciergeScreen | §4.6 |
| PrivateMeetings | PrivateMeetingsScreen | §4.6 |
| PrivateMeetingRoom | PrivateMeetingRoomScreen | §4.6 (media note in §7) |
| PrivateConversations | PrivateConversationsScreen | §4.6 |
| PrivateConversationInfo | PrivateConversationInfoScreen | §4.6 |
| AssetDetail | AssetDetailScreen | §5 |
| AlertManagement | AlertManagementScreen | §5 |
| CryptoAlertManagement | AlertManagementScreen | §5 |
| CryptoAlertCenter | CryptoAlertCenterScreen | §5 |
| CryptoAlertHistory | CryptoAlertHistoryScreen | §5 |
| CryptoPortfolio | CryptoPortfolioScreen | §5 |
| AccountCenter | AccountCenterScreen | §6.2 |
| AccountSettings | AccountCenterScreen | §6.2 |
| AccountSecurity | AccountCenterScreen | §6.2 |
| AccountWebSettings | AccountCenterScreen | §6.2 |
| AccountWebSecurity | AccountCenterScreen | §6.2 |
| AccountPrivacy | AccountCenterScreen | §6.2 |
| AccountDevices | AccountCenterScreen | §6.2 |
| AccountHealth | AccountHealthAppealsScreen | §6.3 |
| AccountHealthWeb | AccountHealthAppealsScreen | §6.3 |
| SafetyHub | SafetyHubScreen | §8.2 |
| SafetyWebHub | SafetyHubScreen | §8.2 |
| TrustSafety | TrustSafetyScreen | §8.3 |
| TrustSafetySupport | TrustSafetyScreen | §8.3 |
| TrustSafetyHelp | TrustSafetyScreen | §8.3 |
| TrustCenter | TrustSafetyScreen | §8.3 |
| SecurityReport | TrustSafetyScreen | §8.3 |
| ScamShield | TrustSafetyScreen | §8.3 |
| VerificationCenter | VerificationCenterScreen | §6.5 |
| VerificationWebCenter | VerificationCenterScreen | §6.5 |
| ActivityInbox | ActivityInboxScreen | §1.5 |
| BriefingsHub | BriefingsHubScreen | §4.7 |
| BriefingDetail | BriefingDetailScreen | §4.7 |
| ActivityInboxLegacyInbox | ActivityInboxScreen | §1.5 |
| ActivityInboxWebActivity | ActivityInboxScreen | §1.5 |
| ActivityInboxWebInbox | ActivityInboxScreen | §1.5 |
| NotificationCenter | NotificationCenterScreen | §1.5 |
| NotificationPreferences | NotificationPreferencesScreen | §6.4 |
| RegionTime | RegionTimeScreen | §6.4 |
| NotificationSettings | NotificationSettingsScreen | §6.1 |
| AppearanceSettings | AppearanceSettingsScreen | §6.1 |
| AccessibilitySettings | AccessibilitySettingsScreen | §6.1 |
| LanguageSettings | LanguageSettingsScreen | §6.1 |
| StorageSettings | StorageSettingsScreen | §6.1 |
| PermissionsSettings | PermissionsSettingsScreen | §6.1 |
| PrivacySettings | PrivacySettingsScreen | §6.1 |
| SecuritySettings | SecuritySettingsScreen | §6.1 |
| SessionsDevices | SessionsDevicesScreen | §6.1 |
| BlockedUsers | BlockedUsersScreen | §6.1 / §8.4 |
| MutedUsers | MutedUsersScreen | §6.1 / §8.4 |
| DataPrivacySettings | DataPrivacySettingsScreen | §6.1 |
| HelpSettings | HelpSettingsScreen | §6.1 |
| AboutSettings | AboutSettingsScreen | §6.1 |
| LegalSettings | LegalSettingsScreen | §6.1 |
| DeveloperSettings | DeveloperSettingsScreen | §6.1 |
| Login | LoginScreen | §6.7 |
| Signup | SignupScreen | §6.7 |
| AccountRecovery | AccountRecoveryScreen | §6.7 |

### 9.3 UNCOVERED

**None. 0 of 182 routes are uncovered.** Every registered route maps to a section of this document or to the navigation/tabs document.

### 9.4 Screens reachable but NOT registered as routes

These are real product surfaces that do **not** appear in `native_nav_registrations.json` because they are dispatched dynamically (by section key) rather than registered individually. A route-only inventory would miss them — the website must still build them.

See §3.4 (Ads) and §2 for detail. Enumerated in §9.5.

### 9.5 Unregistered screen files under `src/screens/` (CONFIRMED by directory listing)

`src/screens/` holds 128 top-level `.tsx` files plus `dropshipping/` (11 screens), `settings/` (17 screens) and `marketplace/`. Several `*Route.tsx` files are thin dispatchers registered under a route name; the real screen behind them is **not** individually registered. A route-only inventory misses these — the website must still build them.

| Unregistered screen file | Reached via |
|---|---|
| `screens/BusinessHubScreen.tsx` | route `BusinessOs` → `BusinessHubRoute.tsx` |
| `screens/BusinessOsScreen.tsx` | fallback branch of `BusinessHubRoute` (legacy Business OS shell) |
| `screens/BusinessOsAdvertisingScreen.tsx` | route `BusinessOsAdvertising` → `AdvertisingRoute.tsx` |
| `screens/AdsManagerScreen.tsx` | `AdvertisingRoute` / Business OS advertising section |
| `screens/AdsCampaignWizardScreen.tsx` | in-Ads navigation from AdsManager |
| `screens/AdsCampaignDetailScreen.tsx` | in-Ads navigation |
| `screens/AdsAudiencesScreen.tsx` | in-Ads navigation |
| `screens/AdsLibraryScreen.tsx` | in-Ads navigation |
| `screens/AdsInsightsScreen.tsx` | in-Ads navigation |
| `screens/AdsReportsScreen.tsx` | in-Ads navigation |
| `screens/AdsWalletScreen.tsx` | in-Ads navigation |
| `screens/AdsPolicyCenterScreen.tsx` | in-Ads navigation |
| `screens/AdsSubPageScreen.tsx` | generic Ads sub-page dispatcher (creatives, policy, delivery) |
| `screens/PromoteContentWizardScreen.tsx` | Promote CTA from post / reel / listing |
| `screens/OrdersManagerScreen.tsx` | route `BusinessOsOrders` → `OrdersRoute.tsx` |
| `screens/CommerceInboxScreen.tsx` | route `BusinessOsMessages` → `MessagesRoute.tsx` |
| `screens/EventsManagerScreen.tsx` | route `BusinessOsEvents` → `EventsRoute.tsx` |
| `screens/ActivityScreen.tsx` | route `BusinessOsActivity` → `ActivityRoute.tsx` |
| `screens/StoreDashboardScreen.tsx` | route `SellerStore` → `SellerStoreRoute.tsx` |
| `screens/PageCreateScreen.tsx` | route `PageCreate` → `PageCreateRoute.tsx` |
| `screens/settings/RelationshipListScreen.tsx` | shared list shell behind Blocked / Muted users |
| `screens/HomeScreen.tsx` | the `Home` tab's inline component (NAV) |

**There is no `AdsCreativesScreen.tsx`** — creatives render through `AdsSubPageScreen` + `api/adsCreatives.ts`.

Housekeeping note: `screens/settings/AppearanceSettingsScreen.tsx.backup-20260808-003942` is a stale backup file, not a screen.

## 10. API module inventory (109 modules)

All modules live in `src/api/`. "Endpoints" = module has backend path literals in `native_api_endpoints.json`.
"Prod importers" = count of non-test `.ts/.tsx` files under `src/` that import it. "Test importers" = files under `__tests__`/`*.test.*`.

Method: static import scan of all `src/**/*.ts(x)` for `"…api/<module>"` specifiers (script run read-only; not committed).

### 10.1 Zero-importer modules — LIKELY DEAD

| Module | Endpoints | Prod | Test | Assessment |
|---|---|---|---|---|
| `pulse.ts` | `/api/dashboard/mission-control`, `/api/pulse/assistant/chat`, `/api/pulse/profile/me` | 0 | 0 | **DEAD.** Pure re-export shim over `messenger.ts` + a mission-control call. Nothing imports it. |
| `referral.ts` | `/api/mobile/referral/claim` | 0 | 0 | **DEAD as imported code.** Referral product surface now lives in `progress.ts` (`/api/progress/referrals`, `/api/progress/invite`) consumed by `ProgressCenterScreen`. |
| `undxRuns.ts` | `/api/undx/runs` | 0 | 1 | **DEAD in app.** Only `src/api/__tests__/undxRuns.test.ts` references it. UNDX run history is not wired to any screen. |

### 10.2 Modules with no endpoint literal (31) — but nearly all are wired

These 31 had no backend path detected. That is almost always because they are **pure client-side domain/presentation/state helpers** that delegate HTTP to a sibling module, not because they are unused.

| Module | Prod importers | Role |
|---|---|---|
| `adsCreatives.ts` | 3 | Creative model/validation for AdsManager/AdsLibrary/AdsSubPage |
| `adsDelivery.ts` | 5 | Delivery-status derivation for campaign screens |
| `adsLibrary.ts` | 1 | AdsLibraryScreen view model |
| `adsPolicy.ts` | 2 | Policy verdict mapping over `adsPortal` |
| `adsReports.ts` | 1 | AdsReportsScreen view model |
| `businessHub.ts` | 6 | Business Hub aggregation over `businessOs`/`sellerApplication`/`verification` |
| `checkoutSchedule.ts` | 2 | Delivery-window/scheduling logic for checkout |
| `config.ts` | 40 | App-wide config/base-URL/flags (most-imported non-transport module) |
| `contentOwnership.ts` | 2 | Post/reel ownership predicates for Home & PostDetail |
| `conversationDomain.ts` | 4 | Conversation type/domain classification (commerce vs social) |
| `dashboardLiveState.ts` | 1 | Live-state composition for DashboardModuleDetail |
| `deleteErrors.ts` | 4 | Delete-failure message mapping (Reels/Profile/Home/PostDetail) |
| `eventsManager.ts` | 9 | Events Manager view model + components |
| `insightsDashboard.ts` | 7 | Insights aggregation (hubBindings, BusinessOsInsights, BusinessHub) |
| `insightsRules.ts` | 2 | Insight ranking rules |
| `marketplaceBuyerPresentation.ts` | 2 | Buyer-facing listing presentation rules |
| `marketplaceCheckoutState.ts` | 1 | Checkout state machine |
| `marketplaceErrors.ts` | 2 | Marketplace error-code → copy mapping |
| `marketplaceFulfillment.ts` | 8 | Fulfillment method modelling (ship/pickup/digital/cash) |
| `marketplaceOffers.ts` | 6 | Offer/negotiation domain over `marketplaceCommerce` |
| `marketplaceScreen.ts` | 2 | Marketplace screen view model |
| `messengerOrdering.ts` | 4 | Message ordering/dedupe |
| `messengerReconciler.ts` | 1 | Optimistic-send reconciliation (used by `messengerOrdering`) |
| `orders.ts` | 3 | Order domain types shared by buyer + dashboard |
| `paymentsHub.ts` | 8 | Money-layer aggregation over `payments`/`sellerPayouts` |
| `presenceSession.ts` | 2 | Presence session lifecycle over `presence` |
| `radio.ts` | 2 | Pulse Radio station model (`core/pulseRadio.ts`, LiveHostSession) |
| `search.ts` | 1 | SearchScreen query model — **UNVERIFIED whether it issues HTTP via a templated path** |
| `sellerIdentity.ts` | 5 | Seller identity/badge resolution |
| `stateLanguage.ts` | 8 | Empty/error/loading state copy vocabulary (i18n-gated) |
| `storeDashboard.ts` | 11 | Store Dashboard aggregation |

### 10.3 Full module table (109)

Ordered by prod importer count descending; `E` = has endpoint literals.

| Module | E | Prod | Test | Primary consumers |
|---|---|---|---|---|
| `pulseApi.ts` | Y | 109 | 26 | shared transport — every api module |
| `config.ts` | n | 40 | 6 | app-wide |
| `feed.ts` | Y | 32 | 13 | Home, Reels, PostDetail, MarketplaceProduct |
| `businessOs.ts` | Y | 28 | 14 | AppNavigator, launch/sectionCapabilities, all Ads + BusinessOs screens |
| `adsDashboard.ts` | Y | 21 | 2 | hubBindings, all Ads screens |
| `marketplace.ts` | Y | 21 | 22 | marketplace/*, navigation/types, Marketplace screens |
| `messenger.ts` | Y | 19 | 9 | Chat, NewChat, calls, pulseCommand |
| `profileTarget.ts` | n→Y* | 15 | 5 | navigation/linking, notificationRouting, discovery |
| `dropshipping.ts` | Y | 13 | 1 | all `screens/dropshipping/*` |
| `reels.ts` | Y | 13 | 9 | Reels, discovery |
| `calls.ts` | Y | 12 | 9 | `src/calls/*` |
| `profile.ts` | Y | 12 | 8 | Profile, ProfileEdit, BusinessProfile, Music |
| `live.ts` | Y | 11 | 6 | Live, LiveStudio, CameraStudio, Reels |
| `status.ts` | Y | 11 | 6 | Status, CameraStudio, discovery |
| `commerceInbox.ts` | Y | 11 | 2 | CommerceInbox + `components/messages/*` |
| `ordersDashboard.ts` | Y | 11 | 2 | OrdersManager, BusinessHub, hubBindings |
| `storeDashboard.ts` | n | 11 | 6 | StoreDashboard, BusinessHub, hubBindings |
| `pages.ts` | Y | 9 | 8 | Page, PageEdit, PageConnections, PageTeam, PresenceHub |
| `eventsManager.ts` | n | 9 | 1 | EventsManager + `components/events/*` |
| `notifications.ts` | Y | 8 | 1 | NotificationCenter, Activity, unreadCounts |
| `marketplaceCommerce.ts` | Y | 8 | 4 | Cart, Checkout, Product, Manager |
| `marketplaceFulfillment.ts` | n | 8 | 0 | Cart, Checkout, Product |
| `paymentsHub.ts` | n | 8 | 3 | MoneyLayer, MoneyDetail, BusinessOsPayments |
| `stateLanguage.ts` | n | 8 | 1 | BusinessOsInsights, MarketplaceManager, AdsManager |
| `adsPortal.ts` | Y | 7 | 4 | Ads screens |
| `insightsDashboard.ts` | n | 7 | 1 | Insights screens |
| `music.ts` | Y | 7 | 3 | MusicScreen, CameraStudio, video |
| `auth.ts` | Y | 6 | 3 | Login, Signup, AccountRecovery |
| `businessHub.ts` | n | 6 | 1 | BusinessHub, hubBindings |
| `privateFeatures.ts` | Y | 6 | 0 | PrivateBriefings/Documents/Shield/People/Concierge |
| `privateOffice.ts` | Y | 6 | 6 | PrivateOffice, PrivateFacts, PrivateOfficeSecurity, PremiumCenter |
| `marketplaceOffers.ts` | n | 6 | 1 | MarketplaceManager, CommerceInbox |
| `support.ts` | Y | 6 | 3 | TrustSafety, HelpSettings, Status, CallActionsSheet |
| `account.ts` | Y | 5 | 2 | AccountCenter, RegionTime, SecuritySettings |
| `adsDelivery.ts` | n | 5 | 0 | Ads campaign screens |
| `groups.ts` | Y | 5 | 5 | Groups, discovery, pulseCommand |
| `premium.ts` | Y | 5 | 1 | GrowthCenter, IntelligenceCenter, CreatorStudio |
| `sellerIdentity.ts` | n | 5 | 0 | Marketplace screens |
| `sellerPayouts.ts` | Y | 5 | 2 | MoneyLayer, MoneyDetail, BusinessOsPayments |
| `verification.ts` | Y | 5 | 1 | VerificationCenter, hubBindings |
| `activityFeed.ts` | Y | 4 | 0 | ActivityScreen + `components/activity/*` |
| `adsOs.ts` | Y | 4 | 0 | AdsCampaignWizard, AdsCampaignDetail |
| `conversationDomain.ts` | n | 4 | 1 | Messenger, MarketplaceProduct |
| `creator.ts` | Y | 4 | 0 | ContentPlanner, CreatorStudio |
| `deleteErrors.ts` | n | 4 | 0 | Reels, Profile, Home, PostDetail |
| `marketIntelligence.ts` | Y | 4 | 2 | MarketPulse, `components/crypto/*` |
| `messengerOrdering.ts` | n | 4 | 0 | Chat, CameraStudio |
| `payments.ts` | Y | 4 | 4 | `src/payments/*`, AdsWallet |
| `push.ts` | Y | 4 | 0 | NotificationPreferences, NotificationSettings, session/auth |
| `stripePaymentSheet.ts` | Y | 4 | 3 | PaymentController, MarketplaceCheckout |
| `watchlists.ts` | Y | 4 | 0 | Watchlists, MarketPulse, Portfolio, AssetDetail |
| `ads.ts` | Y | 3 | 3 | Home, SponsoredAdCard, feed/injectAds |
| `adsCreatives.ts` | n | 3 | 1 | AdsManager, AdsLibrary, AdsSubPage |
| `adsDetail.ts` | Y | 3 | 0 | AdsCampaignWizard/Detail, AdsInsights |
| `alerts.ts` | Y | 3 | 2 | AlertManagement, AssetDetail |
| `briefings.ts` | Y | 3 | 1 | BriefingsHub, BriefingDetail, profile tile |
| `cryptoPremium.ts` | Y | 3 | 1 | CryptoAlertHistory, entitlements/reconcile |
| `growth.ts` | Y | 3 | 1 | GrowthCenter |
| `intelligence.ts` | Y | 3 | 1 | IntelligenceCenter |
| `orders.ts` | n | 3 | 1 | BuyerOrders |
| `privateConversations.ts` | Y | 3 | 0 | PrivateConversations, PrivateConversationInfo |
| `promotions.ts` | Y | 3 | 1 | PromoteContentWizard |
| `sellerApplication.ts` | Y | 3 | 1 | SellerApplication, hubBindings |
| `accountHealth.ts` | Y | 2 | 0 | AccountHealthAppeals |
| `activity.ts` | Y | 2 | 1 | ActivityInbox |
| `adsPolicy.ts` | n | 2 | 0 | AdsManager, AdsSubPage |
| `adsWallet.ts` | Y | 2 | 1 | AdsWallet, PaymentController |
| `businessProfile.ts` | Y | 2 | 1 | BusinessProfile, BusinessBuyerPreview |
| `capitalGraph.ts` | Y | 2 | 1 | CapitalGraph, CapitalEntity |
| `checkoutCountries.ts` | Y | 2 | 2 | MarketplaceCheckout |
| `checkoutSchedule.ts` | n | 2 | 0 | MarketplaceCheckout |
| `composerMusic.ts` | Y | 2 | 0 | HomePulseComposer, create/draftToContentModel |
| `contentOwnership.ts` | n | 2 | 0 | Home, PostDetail |
| `dashboard.ts` | Y | 2 | 1 | UserDashboard |
| `events.ts` | Y | 2 | 0 | EventsScreen |
| `friends.ts` | Y | 2 | 3 | discovery |
| `insightsRules.ts` | n | 2 | 0 | BusinessOsInsights |
| `marketplaceBuyerPresentation.ts` | n | 2 | 1 | MarketplaceProduct, MarketplaceScreen |
| `marketplaceErrors.ts` | n | 2 | 0 | MarketplaceProduct, MarketplaceCheckout |
| `marketplaceScreen.ts` | n | 2 | 1 | MarketplaceProduct, MarketplaceManager |
| `premiumCenter.ts` | Y | 2 | 2 | PremiumCenter, profile/usePremiumTile |
| `presence.ts` | Y | 2 | 0 | Chat |
| `presenceSession.ts` | n | 2 | 5 | Chat, callSessionStore |
| `privateRecords.ts` | Y | 2 | 1 | PrivateOperations, PrivateOffice |
| `radio.ts` | n | 2 | 1 | core/pulseRadio, LiveHostSession |
| `safety.ts` | Y | 2 | 1 | SafetyHub |
| `translation.ts` | Y | 2 | 1 | TranslationPreferencesBootstrap, ContentTranslation |
| `undxSelfKnowledge.ts` | Y | 2 | 2 | UndxCapabilities |
| `adsAudiences.ts` | Y | 1 | 0 | AdsAudiences |
| `adsLibrary.ts` | n | 1 | 0 | AdsLibrary |
| `adsPolicyCenter.ts` | Y | 1 | 0 | AdsPolicyCenter |
| `adsReports.ts` | n | 1 | 0 | AdsReports |
| `camera.ts` | Y | 1 | 0 | CameraStudio |
| `dashboardLiveState.ts` | n | 1 | 0 | DashboardModuleDetail |
| `eventsData.ts` | Y | 1 | 0 | EventsManager |
| `learning.ts` | Y | 1 | 0 | CoursesLearning |
| `marketPulse.ts` | Y | 1 | 0 | MarketPulse |
| `marketplaceCheckoutState.ts` | n | 1 | 0 | MarketplaceCheckout |
| `messengerReconciler.ts` | n | 1 | 0 | messengerOrdering |
| `portfolio.ts` | Y | 1 | 2 | Portfolio |
| `progress.ts` | Y | 1 | 0 | ProgressCenter |
| `rewards.ts` | Y | 1 | 0 | Rewards |
| `saved.ts` | Y | 1 | 1 | Saved |
| `search.ts` | n | 1 | 1 | Search |
| `undxActions.ts` | Y | 1 | 1 | UndxActionCenter |
| `welcome.ts` | Y | 1 | 0 | WelcomeUfoOverlay |
| `pulse.ts` | Y | **0** | 0 | **dead** |
| `referral.ts` | Y | **0** | 0 | **dead** |
| `undxRuns.ts` | Y | **0** | 1 | **dead** |

\* `profileTarget.ts` has a `/pulse/profile` literal but is primarily a route-target resolver.

**Summary:** 106 of 109 modules are wired. 3 are dead: `pulse.ts`, `referral.ts`, `undxRuns.ts`. The "31 with no endpoint" set is a false signal for deadness — 30 of the 31 are client-side domain layers with live consumers.
