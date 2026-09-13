# PulseSoc Native App — Complete Inventory

**The native app is the product source of truth for the web rebuild.** This document is the
canonical native inventory required by the brief. It is assembled from two audits that were
produced separately and are preserved here in full:

- **Part I — Navigation & primary tabs.** The three navigators, the 5-of-15 tab rule, deep
  linking and the URL namespace the app claims.
- **Part II — Product area catalogue.** The ten product areas, coverage of all 182 routes, and
  the 109-module API inventory.

Source: `mobile-native/` (Expo SDK 54, React Native 0.81.5, React 19, TypeScript 5.9, React
Navigation, Zustand). Read-only. Anything not confirmed in source is marked **UNVERIFIED**.

**Headline measurements**

| Measure | Value |
|---|---:|
| TypeScript/TSX source files (`mobile-native/src`) | 1,090 |
| Lines of TypeScript/TSX | 338,546 |
| `*Screen.tsx` files | 147 |
| Distinct registered screen names | 159 |
| Unique navigation routes (185 registrations) | 182 |
| Registered tabs / tabs actually rendered | 15 / **5** |
| Component `.tsx` files (excl. `__tests__`) | 138 |
| API modules (106 wired, 3 dead) | 109 |
| Distinct backend paths called | 330 |

> **Protected systems appear here as inventory only.** Livestream audio, livestream
> infrastructure, audio calls, video calls, call audio routing, the RTC audio-session
> foundation and the Pulse Radio audio foundation are described, never modified, and no web
> implementation is proposed for them in any deliverable.

**Companion documents:** `PULSESOC_NATIVE_TO_WEB_PARITY_MATRIX.md`,
`PULSESOC_WEB_DESIGN_SYSTEM_MAP.md`, `PULSESOC_WEB_API_GAP_ANALYSIS.md`.

---

# Part I — Navigation & primary tabs


Source of truth: `mobile-native/` (Expo 54, RN 0.81.5, React 19, TypeScript, React Navigation, Zustand).
All paths below are relative to `/Users/hmcherie/Desktop/CoinPilotX/mobile-native/` unless stated.
Read-only inventory. Anything not confirmed in code is marked **UNVERIFIED**.

## Section 1 — Navigation architecture

### 1.1 How the three navigators compose

| Level | Navigator | File | Screens |
|---|---|---|---|
| Root switch | `signedIn ? <AppNavigator/> : <AuthNavigator/>` | `App.tsx:420` | — |
| Auth stack | `createNativeStackNavigator<AuthStackParamList>` | `src/navigation/AuthNavigator.tsx:11-15` | `Login`, `Signup`, `AccountRecovery` (all `headerShown:false`) |
| Root stack | `createNativeStackNavigator<RootStackParamList>` | `src/navigation/AppNavigator.tsx:369-730` | 167 routes; first is `Tabs` |
| Tabs | `createBottomTabNavigator<AppTabParamList>` | `src/navigation/AppNavigator.tsx:226-276` | 15 routes, `initialRouteName="Home"` |

There is exactly **one nested navigator**: the Tabs navigator is mounted as the
root-stack screen `Tabs` via a render-prop child (`AppNavigator.tsx:394-396`) so it can
receive `badges`, `identity`, and `onOpenDrawer` props. Everything else in the app is a
sibling on the single root stack — there are no per-tab stacks. Consequence for the web:
the tab bar is a **global chrome element**, not a per-section shell; pushing any of the 167
stack routes replaces the whole viewport including the tab bar.

Stack `initialRouteName` is `"Tabs"` unless the QA fixture flag `PULSESOC_QA_REELS_FIXTURES`
is on, in which case it is `"Reels"` (`AppNavigator.tsx:370`).

### 1.2 THE TAB-VISIBILITY RULE (the real one)

The 15 registered tabs are **not** all rendered. The tab bar is a fully custom `tabBar`
component, `LogiNexusBottomNavigation` (`AppNavigator.tsx:229-240` →
`src/navigation/GlobalNavigation.tsx:221-390`), and it renders **only a hardcoded
5-item array**, `PRIMARY_TABS` (`GlobalNavigation.tsx:71-84`):

| # | Tab | Route | Icon | Refresh destination | Badge |
|---|---|---|---|---|---|
| 1 | Home | `Home` | `home-outline` | `home` | — |
| 2 | Reels | `Reels` | `play-circle-outline` | `reels` | — |
| 3 | Create | `Create` | `add` (becomes `close` when the spatial console is open) | none | — |
| 4 | Messages | `Messenger` | `chatbubble-ellipses-outline` | `social-messages` | unread messages |
| 5 | Profile | `Profile` | `person-circle-outline` | `profile` | — |

**The rule is not role-based, not entitlement-based, not seller-mode-based, and not
remote-configurable.** It is a static module-scope constant. No user attribute, flag, or
role changes the five items. The only per-item dynamic state is:
- `active` highlight from `state.index` (`GlobalNavigation.tsx:286`);
- `disabled` if the tab route is somehow absent from navigator state, except `Create`
  (`:289`);
- the Messages badge count (`:287`);
- the label, which prefers the route's `options.title` (i18n) over the hardcoded label
  (`:382`).

The remaining **10 registered tabs — Dashboard, Search, Saved, Groups, Live, Status,
Notifications, PulseAI, Marketplace, Settings — have no tab-bar item at all.** They are
reachable only by programmatic `navigation.navigate(...)`: the global header's search /
messages / activity / profile icons (`AppNavigator.tsx:249-252`), the master drawer, deep
links, notification routing, and in-screen links.

**Web equivalent:** a 5-item bottom/primary nav (Home, Reels, Create, Messages, Profile)
plus a header (drawer + search + messages + activity + avatar), with the other ten
surfaces as drawer/header destinations only.

`src/navigation/bottomNavPolicy.ts` is a **different** policy — it does not select tabs, it
decides how the dock *behaves* on each tab:

| Policy | Tabs |
|---|---|
| `always-visible` (never auto-hides) | `Dashboard`, `Live`, `PulseAI` |
| `scroll-responsive` (hides on scroll-down, reveals on scroll-up) | `Home`, `Reels`, `Search`, `Saved`, `Groups`, `Status`, `Messenger`, `Notifications`, `Profile`, `Marketplace`, `Settings` |
| `not-rendered` | `Create` |
| default for unknown routes | `scroll-responsive` (`bottomNavPolicy.ts:38`) |

Hide/reveal mechanics live in `BottomNavVisibility.tsx`: keyboard-open always hides
(`:85`); a pin-reason set can override a requested hide; short surfaces never hide
(`:194`); `topRevealY = 88` pins the dock near the top; `hideThreshold = 8` prevents
flicker. `useBottomNavSurface()` (`:238`) returns both the scroll handlers and the bottom
padding so a screen cannot wire one without the other.

### 1.3 The Create tab is a redirect, not a screen

`CreateTabScreen` (`AppNavigator.tsx:200-208`) renders `null` and immediately
`navigate("Home", { openComposer: true })`. The tab-bar `Create` button doesn't even go
through the navigator: it either `toggleCreateConsole()` when `spatialCreateEnabled()`
(the Spatial Create Console, mounted in the tabBar slot at `AppNavigator.tsx:237`), or
`navigate("Home", { openComposer: true })` (`GlobalNavigation.tsx:329-340`). It also
de-bounces double taps within 600 ms. `openPrimaryCreate()` (`GlobalNavigation.tsx:485`)
is the shared helper.

### 1.4 The `*Route.tsx` indirection pattern — CONFIRMED

8 wrapper files exist in `src/screens/`, all registered on the root stack, each choosing
which real screen to render from `route.params`. Comments in the files call it the
"strangler pattern": one registered route name, a new implementation, and the legacy
screen kept exported rather than deleted.

| Registered route | Wrapper | Renders |
|---|---|---|
| `BusinessOs` | `BusinessHubRoute.tsx:46-59` | `BusinessOsScreen` by default; `BusinessHubScreen` for `HUB_LIVE_CARDS` params (deferred `require` to avoid pulling the light theme) |
| `BusinessOsAdvertising` | `AdvertisingRoute.tsx:64-100+` | dispatches on params to `AdsCampaignWizardScreen`, `PromoteContentWizardScreen`, `AdsCampaignDetailScreen`, `BusinessOsAdvertisingScreen`, `AdsAudiencesScreen`, `AdsLibraryScreen`, `AdsPolicyCenterScreen`, `AdsReportsScreen`, `AdsWalletScreen`, `AdsInsightsScreen`, `AdsManagerScreen`, `AdsSubPageScreen` |
| `BusinessOsOrders` | `OrdersRoute.tsx:23` | `OrdersManagerScreen` |
| `BusinessOsMessages` | `MessagesRoute.tsx:16` | `CommerceInboxScreen` |
| `BusinessOsEvents` | `EventsRoute.tsx:33-43` | `ComingSoonScreen` when `routeReadiness`/`GATED_ROUTES` gates it, else `EventsManagerScreen` |
| `BusinessOsActivity` | `ActivityRoute.tsx` | `ActivityScreen` (the rebuilt unified activity feed; legacy `ActivityInbox` route untouched) |
| `SellerStore` | `SellerStoreRoute.tsx:30-35` | `StoreDashboardScreen` when `isStoreDashboardRoute(params)`, else `SellerStoreScreen` |
| `PageCreate` | `PageCreateRoute.tsx:30-40` | `ComingSoonScreen` when gated, else `PageCreateScreen` |

This accounts for **20 of the 21** screens in `src/screens/` that are not a direct
`component=`. The 21st is **`HomeScreen`**, which is not dead either — it is the inline
render-prop child of the `Home` tab (`AppNavigator.tsx:260-262`). **None of the 21 are
dead code.**

Note two of the wrappers are *launch gates*: `EventsRoute` and `PageCreateRoute` consult
`src/launch/readiness.ts` (`GATED_ROUTES`, `routeReadiness`) and can substitute
`src/launch/ComingSoonScreen`. This is the one genuine route-level feature gate in the
navigator.

### 1.5 `masterNavigation.ts` — the URL-shaped catalogue

`src/navigation/masterNavigation.ts` (121 lines) is a **static data table**, not a
navigator. It lists 8 sections × 48 actions, each `{label, route, status, description,
badge?}` where `route` is a **web-style path** (`/pulse/...`, `/dashboard/...`, `/terms`)
and `status` is one of:

| status | meaning |
|---|---|
| `native` | resolves to a native screen |
| `shell` | opens the legacy dashboard web-shell module view |
| `provider` | opens on the pulsesoc.com website (Terms, Privacy Policy) |
| `gated` | declared in the type but unused by any current entry |

`flattenMasterNavigation()` (`:119`) flattens it. This table is what the drawer renders
(Section 2) and is the closest thing in the app to a canonical sitemap — **the web IA
should mirror it directly, because its `route` values are already URLs.**

### 1.6 `GlobalNavigation.tsx` — the app's chrome

937 lines, exporting three pieces of global chrome plus helpers:

| Export | Line | Role |
|---|---|---|
| `LogiNexusGlobalHeader` | `:86` | The global command strip. Left slot = back chevron if `canGoBack`, else hamburger. Centre = title + subtitle + `LivingPulseSocWordmark` in `home` mode. Right = Search, Messages (badged), Activity (badged), avatar. Meta row (non-home) shows a `PulseSoc`/`UNDX` badge, an `attention` badge, and an alerts badge. Modes: `home` \| `standard` \| `intelligence` (`PulseAI` and `IntelligenceCenter` render `intelligence`). |
| `LogiNexusBottomNavigation` | `:221` | The 5-item dock; animated hide (180–220 ms, 0 ms under Reduce Motion), `accessibilityElementsHidden` when hidden; contains the mini player. |
| `PulseMiniPlayerBar` | `:396` (internal) | Persistent Pulse Radio strip above the dock whenever a track is loaded; progress bar, artwork, title/artist, prev / play-pause / next; tapping it navigates the **parent** stack to `PulseQueue`. |
| `openPrimaryCreate` | `:485` | `navigate("Home", { openComposer: true })` |

Tap semantics are delegated to `src/navigation/refreshCoordinator.ts`
(`resolveNavigationTap`) which classifies a tab tap as `root` (scroll to top), `refresh`
(double-tap → `triggerRefreshDestination` with `preserveFilters`/`preserveDrafts`), or a
plain `tabPress` emit + navigate (`GlobalNavigation.tsx:342-363`). A VoiceOver "refresh"
custom action exists on the active tab (`:303-318`).

### 1.7 Presentation modes

Almost every stack route is a **card** push with the global header. Explicit exceptions
found in `AppNavigator.tsx`:

| Route | Option |
|---|---|
| `ContentPreview` | `presentation: "fullScreenModal"`, `headerShown:false` (`:406`) |
| `PulseShare` | `presentation: "modal"` (`:410`) |
| `NativeLiveHost` | `headerShown:false`, `gestureEnabled:false` — cannot be swiped away mid-broadcast (`:576`) |
| `Tabs`, `CameraStudio`, `Call`, `Chat`, `Reels`, `ReelDetail`, all `Dropshipping*`, `MarketplaceProduct`, `BusinessProfile`, `BusinessBuyerPreview`, `MarketplaceManager`, `ReplayViewer`, `PrivateMeetingRoom`, `Rewards`, and ~10 more | `headerShown:false` (screen draws its own header) |

Both the tabs' `sceneContainerStyle` and the stack's `contentStyle` are forced transparent
(`TRANSPARENT_SCENE`, `AppNavigator.tsx:198`) so the single root `PulseBackground` shows
through — pinned by `src/navigation/__tests__/backgroundSurfaces.test.ts`.

## Section 2 — Hamburger / master drawer (COMPLETE ENUMERATION)

Component: `src/components/MasterNavigationDrawer.tsx` (415 lines).
Mounted once, at the root, outside the stack: `AppNavigator.tsx:731`
`<MasterNavigationDrawer visible={drawerOpen} identity={identity} onClose=… onOpenRoute={openDrawerRoute} />`.
Opened by the hamburger in `LogiNexusGlobalHeader` (`GlobalNavigation.tsx:115`), which is
shown whenever the current screen cannot go back (`showDrawer={!back}`,
`AppNavigator.tsx:379`) — i.e. on every tab, and on the first card of any pushed flow.

### 2.1 Drawer chrome (non-destination controls)

| Control | Behaviour | File:line |
|---|---|---|
| Scrim (tap outside) | closes drawer | `MasterNavigationDrawer.tsx:45` |
| `×` close button | closes drawer | `:57` |
| Identity block (`DrawerIdentity`) | avatar/initials, display name, `@username`, `verified` badge, `premium` badge | `:131-156` |
| Title / subtitle | "Search and jump to anywhere in PulseSoc." | `:53` |
| **Search field** | free-text filter over `section.title + label + description + route + status + badge` | `:24-35`, `:65` |
| Count badge | `"{N} actions"` — currently **48** | `:73` |
| `server authoritative` badge | static safety-tone badge | `:74` |
| Section header (tap) | collapse/expand that section; collapse is suppressed while a query is active | `:81-92`, `:78` |
| Per-section count | number of actions | `:91` |
| Per-item row | label, `status` pill, the raw route path shown as secondary text, optional `badge` | `:95-112` |
| Empty state | "Try a module, route, or subsystem name." | `:121` |
| Section tone | `creator` (Creator/Business, Content), `economy` (Economy), `safety` (Trust), `intelligence` (Intelligence), default otherwise | `:167-172` |

**Gating: there is NONE at the drawer level.** Every one of the 48 entries is rendered for
every signed-in user, regardless of role, entitlement, seller mode, or feature flag.
`openAction` (`:37-40`) unconditionally calls `onOpenRoute(action.route)` →
`openDrawerRoute` (`AppNavigator.tsx:362-365`) → `openNativeRoute(navigationRef, path)`.
Gating happens **at the destination**, not in the menu (launch-readiness `ComingSoonScreen`
substitution, premium paywalls inside `PremiumScreen`, seller-onboarding gates inside
`MarketplaceCreateGateway` / `SellerStore`). The web should copy this: show the whole map,
gate on arrival.

The `status` values are shown as pills and only tell the user where the destination lives.
`gated` is declared in the type (`masterNavigation.ts:1`) but **used by zero entries**.

### 2.2 Every destination, all 48

Route resolution below is from `src/navigation/nativeRouteActions.ts:129-216`, falling
through to `src/navigation/dashboardRouting.ts:33-237`.

#### Primary — "Core PulseSoc command surfaces."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| Home | `/pulse` | native | `Tabs → Home` | none |
| Dashboard | `/pulse/dashboard` | native | `Tabs → Dashboard` | none |
| Search | `/pulse/search` | native | `Tabs → Search` (`?q=`/`?query=` → `query` param) | none |
| Activity Inbox | `/pulse/activity` | native | stack `ActivityInbox` (`?category=` ∈ all, messages, calls, social, safety, verification, marketplace, creator_growth, intelligence_alerts) | none |
| Settings | `/pulse/settings` | native | `Tabs → Settings` | none |

#### Social — "Messaging, identity, network, and communities."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| Messages | `/pulse/messages` | native | `Tabs → Messenger` | none |
| Calls | `/pulse/activity?category=calls` | native | `ActivityInbox {category:"calls"}` | none |
| Profile | `/pulse/profile` | native | `Tabs → Profile` | none |
| Profile Edit | `/pulse/profile/edit` | native | stack `ProfileEdit` (special-cased first, `nativeRouteActions.ts:132`) | none |
| Groups | `/pulse/groups` | native | `Tabs → Groups` | none |
| Saved | `/pulse/saved` | native | `Tabs → Saved` | none |

#### Creator / Business — "Publishing, growth, learning, and scheduled content."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| Create Post | `/pulse/compose` | native | `Tabs → Home {openComposer:true}` | none |
| Camera | `/pulse/camera/photo?target=feed` | native | stack `CameraStudio {mode:"photo", target:"feed"}` | camera permission at runtime |
| Creator Studio | `/pulse/creator-studio` | native | stack `CreatorStudio` | none |
| Content Planner | `/dashboard/creator/content-planner` | shell | stack `ContentPlanner {mode:"planner"}` (`dashboardRouting.ts:196`) | none |
| Draft Studio | `/dashboard/creator/draft-studio` | shell | stack `ContentPlanner` in draft mode (`dashboardRouting.ts:204`) | none |
| Growth Center | `/pulse/growth` | native | stack `GrowthCenter` | none |
| Courses | `/pulse/courses` | native | stack `Courses` | none |
| Events | `/pulse/events` | native | stack `Events` | none |

#### Content — "Feed, reels, status, live, and media."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| Reels | `/pulse/reels` | native | `Tabs → Reels` | none |
| Status | `/pulse/status` | native | `Tabs → Status` | none |
| Add Status | `/pulse/status/create` | native | `Tabs → Status {openCreator:true}` | none |
| Live Viewer | `/pulse/live` | native | `Tabs → Live` | none |
| Live Studio | `/pulse/live/studio` | native | stack `LiveStudio` via the dashboard fallback (`dashboardRouting.ts:61`) | camera/mic permission |
| Pulse Radio | `/pulse/music#pulse-radio` | native | stack `Music` (`#music-upload` → `openUpload:true`; `?track=`, `?artist=` supported) | none |

#### Economy — "Marketplace, seller, buyer, premium, and orders."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| Marketplace | `/pulse/marketplace` | native | `Tabs → Marketplace` | none |
| Seller Store | `/pulse/seller-store` | native | stack `SellerStore` → `SellerStoreRoute` (`StoreDashboardScreen` when `isStoreDashboardRoute(params)`, else `SellerStoreScreen`); accepts `?seller_id=` | seller onboarding inside the screen |
| Create Listing | `/pulse/marketplace/create` | native | stack `MarketplaceCreateGateway` | gateway screen enforces seller eligibility |
| Seller Inventory | `/dashboard/economy/seller-tools` | shell | stack `SellerStore` (`dashboardRouting.ts` `/seller-tools`) | same |
| Buyer Orders | `/pulse/orders` | native | stack `BuyerOrders` | none |
| Premium | `/pulse/premium` | native | stack `Premium` | paywall inside the screen |

#### Intelligence — "UNDX, alerts, crypto, watchlists, and safety intelligence."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| UNDX (badge `LogiNexus`) | `/pulse/ai` | native | `Tabs → PulseAI` | none |
| UNDX Action Center | `/pulse/undx/actions` | native | stack `UndxActionCenter` (`?org_id=`, `?actor=`, `?product_area=`) | none |
| Intelligence Center | `/pulse/intelligence` | native | stack `IntelligenceCenter` (header renders in `intelligence` mode) | none |
| Alert Management | `/pulse/alerts` | native | stack `AlertManagement` | none |
| Crypto Command | `/dashboard/crypto/alerts` | shell | stack `AlertManagement` (`/alerts` match) | none |
| Watchlists | `/dashboard/crypto/watchlists` | native | stack `Watchlists` | none |
| Portfolio | `/pulse/portfolio` | native | stack `Portfolio` (no `title` param on purpose, for i18n) | plan holding limit inside the screen |
| Scam Shield | `/scam-shield/scan` | native | stack `ScamShield` | none |

#### Trust — "Identity, account, safety, verification, and support."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| Account Center | `/dashboard/account/settings` | native | `Tabs → Settings` (`dashboardRouting.ts:184`) | none |
| Security Center | `/dashboard/account/security` | native | stack `AccountCenter {section:"security"}` (`dashboardRouting.ts:180`) | none |
| Privacy Center | `/pulse/settings/privacy` | native | stack `AccountPrivacy` | none |
| Verification | `/pulse/verification` | native | stack `VerificationCenter` | none |
| Account Health | `/pulse/account-health` | native | stack `AccountHealth` (also `/dashboard/account/health`, `/account/health`) | none |
| Safety Hub | `/pulse/safety` | native | stack `SafetyHub` | none |
| Support | `/pulse/support` | native | stack `TrustSafetySupport` (also `/support`) | none |

#### Utility — "Provider and legal boundaries."
| Label | Path | status | Resolves to | Gate |
|---|---|---|---|---|
| Notifications | `/pulse/notifications` | native | stack `NotificationCenter`; with `?briefing=<id>` → `BriefingDetail` | none |
| Notification Preferences | `/dashboard/network/notifications` | shell | stack `NotificationPreferences` (`dashboardRouting.ts:66`) | none |
| Terms | `/terms` | provider | `Tabs → Settings` (**does not open a web view** — `nativeRouteActions.ts:214`) | none |
| Privacy Policy | `/privacy` | provider | `Tabs → Settings` (same line) | none |
| System Status | `/dashboard/system/feed` | shell | `DashboardLegacyModule` catch-all (`dashboardRouting.ts:237`) | none |

> Note the `provider` status is aspirational: both Terms and Privacy Policy currently land
> on the Settings tab rather than pulsesoc.com. **Web rebuild must provide real `/terms`
> and `/privacy` pages regardless.**

### 2.3 Reachable from the drawer but not *in* it

The drawer is the only global menu; there is no secondary hamburger. Additional global
shortcuts live in the header beside it: Search → `Tabs→Search`, Messages →
`Tabs→Messenger`, Activity → `ActivityInbox`, avatar → `Tabs→Profile`
(`AppNavigator.tsx:382-387`, tab variant `:249-252`). The Pulse Radio mini player (above
the dock) is a sixth persistent global control → `PulseQueue`.

## Section 3 — Home tab (in depth)

### 3.1 What actually renders

The `Home` tab is registered with a render-prop child, not a `component=`, so it can be
handed the badge and identity props:

```
AppNavigator.tsx:260-262
<Tabs.Screen name="Home" options={{ headerShown: false, title: t("common:tabs.home") }}>
  {() => <HomeScreen badges={badges} identity={identity} />}
</Tabs.Screen>
```

→ **`src/screens/HomeScreen.tsx`, 3,287 lines** (`HomeScreen` exported at `:127`).
`headerShown:false` because Home draws its own `HomeTopBar` (`:1292`) rather than the
global header.

### 3.2 Layout skeleton

`HomeScreen` renders a single `FlatList` (or `SpatialPager` when
`spatialHomeFeedEnabled()`) whose `ListHeaderComponent` is the memoized `HomeHeader`
(`:1097`). Layout is width-responsive:

| Breakpoint | Effect |
|---|---|
| `width < 360` | `compactHero` — condensed Pulse Network hero |
| `width >= 900` | `wideCanvas` — adds `HomeCommandRail` (left) and `HomeWebSideRail` (right), a three-column layout |

Header stack top→bottom: `HomeTopBar` → [`HomeCommandRail`] → `PulseNetworkHero` →
`StatusRail` → `HomePulseComposer` → feed-tab chip strip → [`HomeWebSideRail`].

### 3.3 Feed tabs / filters — 11 of them

`FEED_TABS` (`HomeScreen.tsx:80-91`), rendered as a horizontal chip `ScrollView`
(`:1197-1205`). The selection persists to AsyncStorage key
`pulsesoc.native.home.feed.selection.v1` (`:93`, restored at `:357`).

| key | label | description |
|---|---|---|
| `for_you` | For You | Ranked PulseSoc feed (default) |
| `following` | Following | Accounts you follow |
| `friends` | Friends | Friend graph updates |
| `communities` | Communities | Groups and rooms |
| `trending` | Trending | Active public signals |
| `crypto` | Crypto | Market and crypto signals |
| `scam_alerts` | Scam Alerts | Safety and scam signals |
| `arena_highlights` | Arena Highlights | Arena clips and moments |
| `roast_clips` | Roast Clips | Creator clips and comedy |
| `questions` | Questions | Questions and answers |
| `my_posts` | My Posts | Your published posts |

Feed data: `listFeed` / `loadCachedFeed` from `src/api/feed.ts` → **`GET /api/pulse/posts`**.
Offline-first: a cached feed renders before the network answers. A "**New Signals
available**" pill (`:1021-1029`) appears when fresh items arrive while the user is scrolled
away from the top, rather than jumping the list.

### 3.4 Sub-surfaces, component by component

| Surface | Component (file:line) | What it shows / does | API module → endpoints |
|---|---|---|---|
| Top bar | `HomeTopBar` (`HomeScreen.tsx:1292`) → `LogiNexusGlobalHeader` in `home` mode | hamburger, `LivingPulseSocWordmark` (`components/home/LivingPulseSocWordmark.tsx`), search, messages (badged), activity (badged), avatar | `core/unreadCounts` |
| Command rail (≥900pt) | `HomeCommandRail` (`:1221`), items in `HOME_COMMAND_ITEMS` (`:98-105`) | Home `/pulse`, Dashboard `/pulse/dashboard`, Reels `/pulse/reels`, Videos `/dashboard/media/video-library`, Premium `/pulse/premium`, Saved `/pulse/saved`; plus a Pulse Radio entry | routes via `openNativeRoute` |
| **Pulse Network hero** | `PulseNetworkHero` (`:1323`) | `LogiNexusBadge "Pulse Network"`; a health pill reading `Connected` / `Cached` that refreshes on tap; a "mood" line (`Curious`/`Cached`) + aggregate summary; `GalacticAtmosphere variant="feed"` backdrop | derived from feed + status, no own call |
| Hero metrics | `HeroMetricBlock` (`:1558`) ×3 | **Signals** (post count, or `Cached`/`—`), **creators** (distinct authors across posts+statuses), **live** (statuses with `author_live`/`status_type==="live"`) → tapping *live* opens the Live tab | derived |
| Hero quick tiles | `HeroTile` (`:1516`) ×3 | **UNDX** (alert count → PulseAI), **Pulse Radio** (→ radio library), **Safety Shield** (alert count → Safety) | derived; `alertCount` = posts matching `/scam\|alert\|warning\|security\|safety/i` |
| Pulse Radio inline control | `PulseRadioHeroControl` (`:1394`) | play/pause in the hero; knows `connecting`/`buffering`/`error`/`offline` and whether playback was `interruptedBy` something | `core/pulseRadio` |
| **Status rail** | `StatusRail` (`:1584`), placeholder `StatusPlaceholder` (`:1641`) | horizontal status ring: add-status entry, per-author status bubbles, view-all | `api/status.ts` → `GET /api/pulse/status`, `…/{id}/view`, `…/{id}/react`, `…/{id}/reply`, `…/{id}/share`, `…/ai-story` |
| **Composer entry point** | `components/HomePulseComposer.tsx` | expands inline; modes `post` \| `status` \| `reel`; callbacks `onOpenCamera(mode, composerMode)`, `onOpenMusic(composerMode)`, `onOpenPreview(token)`, `onOpenRoute`, `onCreated(post)`; auto-expands from `route.params.openComposer` and from the `Create` tab; `captureReturnNonce` / `shareHandoffNonce` carry a camera capture or an OS share back into it | `api/feed.ts` → `POST /api/pulse/posts` |
| Feed rows | `FlatList` in `HomeScreen` | `HomeRow<PulsePost>` union of feed rows, discovery rows, and injected ads | |
| Ads | `components/SponsoredAdCard.tsx`, `feed/injectAds.ts`, `api/ads.ts` | interleaved sponsored cards | `GET`/`POST /api/pulse/ads/impression`, `/click`, `/event`, `/viewability` |
| **Discovery rows** | `discovery/discoveryRows.ts`, `discovery/DiscoveryRowView.tsx`, `discovery/useHomeDiscovery.ts`, `discovery/sources.ts` | 7 module kinds: `reels`, `people`, `creators`, `statuses`, `groups`, `topics`, `sponsored`. Placement: lead-in after 5 rows, then every 7, min 3 items/module, max 4 rows, round-robin so no kind monopolises slots (`DISCOVERY_LEAD_IN/INTERVAL/MIN_ITEMS/MAX_ROWS`). Per-module and per-person dismissal persisted (`discovery/dismissals.ts`). Gated by `homeDiscoveryEnabled()` (`discovery/flags.ts`) — off = zero fetches. Impression analytics in `discovery/analytics.ts`. | `api/reels.ts` (`listReels`), `api/status.ts` (`listStatuses`), `api/groups.ts` (`listGroups`), `api/friends.ts` (`listSuggestedPeople` → `/api/pulse/friends`); actions `sendFriendRequest` → `POST /api/pulse/friends/request`, `joinGroup` |
| Intelligence side rail (≥900pt) | `HomeWebSideRail` (`:1428`) | "PulseSoc Intelligence" panel (posts today, community mood, progress bar); "Trending Signals" card → UNDX; a "Sponsored Signal" placeholder card → Safety Shield; "Realtime layer ready" panel | derived |
| Drawer | `MasterNavigationDrawer` — Home mounts its **own** instance (`HomeScreen.tsx:30`) in addition to the root one | see Section 2 | — |

### 3.5 Post card anatomy and EVERY interaction

`src/components/PostCard.tsx` (1,841 lines). Root `testID={`home-feed-post-${post.id}`}` (`:278`).

**Header region**
| Element | testID / line | Behaviour |
|---|---|---|
| Author block (avatar, name, handle, timestamp) | `home-feed-author-${id}` `:287` | → `ProfileDetail` via `profileTargetFromPost` / `profileNavigationParams` |
| "Pulse Creator" label | `:226` | shown when `premium_verified` or `verified` |
| Automated-account pill | `:304` | `accessibilityLabel="Automated PulseSoc account"` |
| **Follow / Following** | `home-feed-follow-${id}` `:333-335` | `toggleFollowAuthor` → `POST /api/pulse/follows/toggle` |
| Overflow `⋯` | `home-feed-overflow-${id}` `:349` | opens the options sheet |

**Body / media**
| Element | line | Behaviour |
|---|---|---|
| Body text | `:400` | "Read full post" / "Collapse post" toggle |
| Media tiles | `home-feed-media-${id}-${i}` `:917`, `:959` | open the media viewer (`onOpenViewer`) |
| Video | `:1132`, `:1162` | "Open video"; per-card mute/unmute |
| Live stage | `:418`, `:421` | "Mute Live"/"Unmute Live" in-card, and "Open full Live" → `onOpenLive(post)` |

**Action bar**
| Action | testID | API |
|---|---|---|
| Like / Liked | `home-feed-like-${id}` `:485-487` | `reactToPost` → `POST /api/pulse/posts/{id}/react` |
| **Reaction selector** (long-press) | `home-feed-reaction-selector-${id}` `:583` | 6 reactions (`:1183-1188`): 👍 Like, ❤️ Love, 🔥 Fire, 😂 Funny, 😮 Wow, 🚀 Rocket |
| Comment | `home-feed-comment-${id}` `:506` | opens the inline composer / `PostDetail` |
| Repost | `home-feed-repost-${id}` `:522-524` | `repostPost` → `POST /api/pulse/posts/{id}/repost` |
| Share | `home-feed-share-${id}` `:538` | `sharing/nativeShare.ts` `sharePulseObject`, URL from `pulsePostUrl` |
| Save / Saved | `home-feed-save-${id}` `:559-561` | `social/savedStore.ts` `peekSaveState`, `social/useSaveAction.ts` `setSaved` |

**Inline comment composer** — `home-feed-inline-comment-${id}` (`:751`): text input
(`:761`), emoji button (`:777`), submit (`:790`) → `addPostComment` →
`POST /api/pulse/posts/{id}/comments`.

**Overflow sheet** (`:612-712`), dismiss at `home-feed-overflow-dismiss-${id}`:
| Item | testID | API |
|---|---|---|
| Promote | `home-feed-promote-${id}` `:628` | → `GrowthCenter` / promote wizard |
| Report | `home-feed-report-${id}` `:644` | `POST /api/pulse/report` |
| Hide | `home-feed-hide-${id}` `:660` | `hidePost` → `POST /api/pulse/posts/{id}/hide` |
| Block | `home-feed-block-${id}` `:676` | block author |
| Mute | `home-feed-mute-${id}` `:692` | `mutePostAuthor` → `POST /api/pulse/users/mute` |
| Delete | `home-feed-delete-${id}` `:708` | `deletePost` → `DELETE /api/pulse/posts/{id}`; shown only when `isContentOwner` (`api/contentOwnership.ts`); errors phrased by `api/deleteErrors.ts` `describeDeleteError` |

All mutating taps go through `social/actionGuard.ts` (`useSocialActionGuard`, `actionKey`)
so a double tap cannot double-post.

### 3.6 Drill-down destinations from Home

| From | To (stack route) |
|---|---|
| Author / avatar / mention | `ProfileDetail` |
| Post tap / comment | `PostDetail` |
| Status bubble | `StatusDetail`, or `Tabs→Status` |
| Live metric / live post | `Tabs→Live`, `LiveDetail` |
| UNDX hero tile, Trending Signals card | `Tabs→PulseAI` |
| Safety Shield tile / sponsored card | `SafetyHub` (`/pulse/safety`) |
| Pulse Radio tile / hero control | `Music` (`/pulse/music`), `PulseQueue` |
| Composer camera | `CameraStudio` |
| Composer preview | `ContentPreview` (fullScreenModal) |
| Composer music | `Music` |
| Command rail | `/pulse/dashboard`, `/pulse/reels`, `/dashboard/media/video-library`, `/pulse/premium`, `/pulse/saved` |
| Discovery reels row | `Reels` / `ReelDetail` (via `discovery/reelTransfer.ts` `stageReelTransfer`) |
| Discovery groups row | group detail / join |
| Share sheet | OS share |

### 3.7 Cross-cutting Home behaviours

- **Refresh registration:** `registerRefreshDestination("home", …)` (`navigation/refreshCoordinator.ts`) — a second tap on the Home dock item scrolls to top, a double tap refreshes while preserving filters and drafts. `homeReselect.ts` holds the reselect contract.
- **Event sync:** `core/eventSync.ts` `registerSyncInvalidation` / `invalidateNativeSync` keeps the feed, statuses and badges coherent with pushes.
- **Ambient motion:** `useHomeAmbientMotionEnabled()` (`:108`) reads `expo-battery` + `AccessibilityInfo` and disables hero animation on low power / reduce-motion.
- **Spatial mode:** `spatial/flags.ts` `spatialHomeFeedEnabled()` swaps the `FlatList` for `spatial/SpatialPager.tsx`; in that mode the outer list carries no rows (`EMPTY_FEED_ROWS`, `:96`).
- **Dock coupling:** `useBottomNavScrollVisibility` + `useBottomNavContentPadding`.

## Section 4 — Reels, Create, Messages, Profile tabs

### 4.1 Reels — `src/screens/ReelsScreen.tsx` (1,609 lines)

Registered twice: tab `Reels` (`AppNavigator.tsx:267`) and stack `Reels` + `ReelDetail`
(`:413-414`), all `headerShown:false`.

**Feed mechanics / playback**
- Vertical full-screen pager; per-item card is `src/components/ReelPlayerCard.tsx`.
- Offline-first: `loadCachedReelsSnapshot` then `listReels`.
- View tracking: `trackReelView` → `POST /api/pulse/reels/{id}/view`.
- Dock policy `scroll-responsive`, with an immersive 220 ms hide / 180 ms reveal retiming applied **only** on Reels when `immersiveNavigatorEnabled()` (`GlobalNavigation.tsx:247`).
- Reselect contract in `src/navigation/reelsReselect.ts`; refresh destination `reels`.
- QA fixtures switch: `PULSESOC_QA_REELS_FIXTURES` (also flips the stack's `initialRouteName`).
- Translation overlay: `src/components/ContentTranslation.tsx`.

**Interactions** (all from `src/api/reels.ts`)
| Interaction | Function | Endpoint |
|---|---|---|
| React / like | `reactToReel` | `POST /api/pulse/reels/{id}/react` |
| Comment: list / add / edit / delete | `getReelComments`, `addReelComment`, `editReelComment`, `deleteReelComment` | `/api/pulse/reels/{id}/comments`, `/api/pulse/reels/comments/{commentId}` |
| React to a comment | `reactToReelComment` | `/api/pulse/reels/comments/{commentId}/react` |
| Comment drafts (persisted) | `saveReelCommentDraft`, `loadReelCommentDraft`, `clearReelCommentDraft` | local |
| Repost | `repostReel` | `POST /api/pulse/reels/{id}/repost` |
| Share | `shareReel`, `reelWebUrl` | `POST /api/pulse/reels/{id}/share` |
| Save | `social/useSaveAction` `setSaved` | shared save store |
| Follow creator | `followReelCreator` | `POST /api/pulse/reels/{id}/follow-creator` |
| Not interested / "show less" | `markReelNotInterested` | `POST /api/pulse/reels/{id}/not-interested` |
| Report reel / report comment | `reportReel`, `reportReelComment` | `POST /api/pulse/report` |
| Delete own reel | `deleteReel` (confirm dialog, `describeDeleteError`) | |
| Overflow sheet | `ReelMoreMenu` (`ReelsScreen.tsx:1072`) | Repost · Show less · Report · Promote · Delete |

**Drill-downs**
| Trigger | Destination |
|---|---|
| Create `＋` button (top-right, `:1014`) | `Tabs → Home {openComposer:true, composerMode:"reel"}` |
| Author tap (`:888`) | `ProfileDetail` (params via `profileTargetFromAuthor`) |
| Promote (`:880`, `:1072`) | `GrowthCenter {contentType:"reel", contentId}` |
| Live reel (`:850`) | `LiveDetail` |
| Replay (`:841`) | `ReplayViewer` |
| "Join this Live" on the card | live join |

### 4.2 Create — `CreateTabScreen` + `HomePulseComposer` + `CameraStudioScreen`

**The Create *tab* is a 6-line redirect** (`AppNavigator.tsx:200-208`, see §1.3). The real
creation surface is `src/components/HomePulseComposer.tsx` (1,489 lines) mounted inside
the Home header, plus `src/screens/CameraStudioScreen.tsx` (1,101 lines).

**Creation modes** (`HomePulseComposer.tsx:42-50`)
| Group | key | label | note |
|---|---|---|---|
| Primary | `post` | Feed | Publish a PulseSoc feed signal |
| Primary | `status` | Status | 24-hour Status, same media pipeline |
| Primary | `reel` | Reel | Attach one video or record a Reel |
| Secondary | `poll` | Poll | Ask the community a question (body must end in `?`, enforced at `:263`) |
| Secondary | `scam_report` | Scam Alert | Detailed warning routed to moderation |

Plus two hand-offs out of the composer (`PRODUCTION_CREATION_ROUTES`, `:52-55`):
**Marketplace** → `/pulse/marketplace/create`, **Question** → `/pulse/questions`.

**Composer controls** (testIDs are the contract)
| Control | testID |
|---|---|
| Collapsed create pill / expand | `home-composer-create-compact`, `home-composer-expand` |
| Collapse | `home-composer-collapse` |
| Text input | `home-composer-input` |
| Character counter | `home-composer-counter` |
| **Audience / visibility** — `public` \| `followers` \| `private` (`VISIBILITY`, `:56`) | `home-composer-audience`, `home-composer-audience-options` |
| Camera | `home-composer-camera` |
| Video | `home-composer-video` |
| Gallery | `home-composer-gallery` |
| Music picker | `home-composer-music-picker` (close: "Close music picker") |
| Status shortcut | `home-composer-status` |
| More tools | `home-composer-more`, `home-composer-more-tools` |
| Preview before publishing | → `ContentPreview` (fullScreenModal) |
| Publish | `home-composer-publish` |
| Retry a failed publish | `home-composer-retry` |
| Recovered draft banner | `home-composer-recovered-draft` |
| Clear draft | `home-composer-clear-draft` |

**Drafts:** auto-persisted to AsyncStorage `pulsesoc.native.home.composer.draft.v1`
(`:41`, write at `:229-246`). A draft exists when any of body / topic / music track /
media / non-public visibility / non-post mode is set (`:119`). Legacy single-media drafts
are migrated to the multi-item queue on load (`:136-138`). A failed publish is stored on
the draft (`lastFailedPublish`) so Retry survives a relaunch.

**Media / upload lifecycle:** `useComposerMediaQueue({contextType:"pulse", target:"feed",
destination:"feed", mode:"post"})` (`:116`) holds the multi-item queue with per-item upload
results; `create/draftToContentModel.ts` (`ComposerDraftInput`) converts a draft into the
content model at publish time; `uploadResultMediaId` confirms a server media id before the
draft is called "ready". Topic/mood: "Composer mood and emoji options". Music attachment
is a first-class draft field (`musicTrack`).

**Hand-offs into the composer:** `captureReturnNonce` (a `CameraStudio` capture returning)
and `shareHandoffNonce` (an OS share-sheet payload, `:186-201`) both re-enter the composer
without losing the draft.

**Camera Studio** (`screens/CameraStudioScreen.tsx`) — 8 destinations
(`DestinationKey`, `:55`, table at `:68-75`) × 3 native capture modes (`photo`, `video`,
`live`):

| key | label | target | default mode | helper | provider route |
|---|---|---|---|---|---|
| `feed` | Feed | feed | photo | Post to PulseSoc feed | `/pulse/camera/post` |
| `status` | Status | status | photo | (status) | `/pulse/camera/status` |
| `reel` | Reel | reel | reel | Create a Reel | `/pulse/camera/reel` |
| `avatar` | Avatar | avatar | photo | Update profile photo | `/pulse/camera/photo?target=avatar` |
| `cover` | Cover | cover | photo | Update profile cover | `/pulse/camera/photo?target=cover` |
| `message` | Message | message | photo | Send to Messenger | `/pulse/camera/photo?target=message` |
| `creator` | Creator | creator | photo | Creator tools | `/pulse/camera` |
| `marketplace` | Market | marketplace | photo | Marketplace tools | `/pulse/camera` |

Video capture additionally requires microphone permission (`:198`); compression is chosen
per destination by `cameraCompressionPolicy` (`:129`, `:136`); gallery picking via
`mediaUpload.chooseVideo()` / `chooseImage()` (`:323`).

**Other creation entry points** (not in the Create tab): Status creator
(`Tabs→Status {openCreator:true}`), Live Studio (`/pulse/live/studio` → `LiveStudio`),
Marketplace listing (`MarketplaceCreateGateway`), Group/Room (`Tabs→Groups` with
`community/communityCreateIntent.ts`), Page (`PageCreate`), Ad campaign
(`BusinessOsAdvertising` wizard), and the flag-gated `SpatialCreateConsole`.

### 4.3 Messages — `src/screens/MessengerScreen.tsx` (468 lines) + `ChatScreen.tsx` (3,106)

Tab `Messenger`, `headerShown:false` (`AppNavigator.tsx:270`). Conversation view is the
**stack** route `Chat` (`:408`), also `headerShown:false`.

**Transport (read-only description — do NOT modify):** HTTP over
`src/api/messenger.ts`, base `const MESSENGER_API = "/api/pulse/communications/v2"`
(`:27`). The list screen subscribes to an in-process store
(`subscribeConversationUpdates`, `:951`) fed by `core/eventSync`; the conversation view
**polls**, with the server dictating the cadence via `poll_interval_ms` on the
conversation response (`:176`) and `syncConversation(conversationId, afterId)` (`:628`)
doing incremental catch-up. Typing is published with `sendTyping` (`:794`) and read back
from `presence.typing[]` (`:136`, `:174`). There is no websocket in this path. Real-time
audio/video lives in the separate `Call` route.

**Inbox / conversation list**
| Element | Detail |
|---|---|
| Segment rail | `all`, `direct`, `groups`, `rooms`, `ai`, `unread` (badged with `unreadTotal`) — `MessengerScreen.tsx:168-173`, rendered by `components/PulseCommand.tsx` `PulseCommandSegmentRail` (`:230`) |
| Domain split | `conversationSplitEnabled()` (`api/conversationDomain.ts`); when on, the tab shows only `conversation_domain === "SOCIAL"` (`:126`), commerce threads live in `BusinessOsMessages` → `CommerceInboxScreen` |
| Scope fetch | `listConversations("social")` (`:97`), cached via `loadCachedConversations` |
| Presence strip | horizontal "active now" row → `Chat` with `presence` (`:239`) |
| Row rendering | `pulseCommand/domain.ts`: `conversationDisplayTitle`, `conversationPreview`, `conversationTime`, `conversationSignalBadges`, `isActivePresence`, `isAssistantPresence`, `conversationAccessibilityLabel` |
| PulseAI thread | pinned assistant conversation `PULSE_AI_CONVERSATION_ID` / `PULSE_AI_DISPLAY_NAME` (`:344`) |
| Quick actions | **Create Group** and **Start Room** → set `communityCreateIntent` then `Tabs→Groups` (`:248-249`) |
| New chat | `navigate("NewChat", {initialQuery})` (`:82`) |
| Search | `searchMessenger`, `searchSocialConversations`, `searchMessengerUsers`, `searchConversationMessages` |
| Refresh | `registerRefreshDestination("social-messages", …)` |
| Visual refresh flag | `spatial/flags.ts` `messagesVisualRefreshEnabled()` |

**Message-level actions** (`api/messenger.ts`)
| Action | Function | Endpoint |
|---|---|---|
| Send (with `reply_to_message_id`, `reply_preview`, `attachment_ids`) | `sendConversationMessage` | `POST …/conversations/{id}/messages` |
| Offline queue + drain | `enqueueMessengerMessage`, `drainMessengerQueue` | local → replay |
| React | `reactToMessage(messageId, "pulse")` | `POST …/messages/{id}/reactions` |
| Delete (`self` \| `everyone`) | `deleteMessage` | `DELETE …/messages/{id}` |
| Report | `reportMessage` | `POST …/messages/{id}/report` |
| Mark seen / read receipts | `markConversationSeen` | |
| Typing | `sendTyping` | |
| Forward | `forwarded` message flag | |

**Conversation settings — the "Control Center"**
`getConversationControlCenter` (`:801`), `updateConversationControlSetting(section, key,
value)` (`:806`), `listConversationMembers` (`:814`), `listConversationControlMedia(kind)`
(`:819`), `listConversationControlLinks` (`:826`), `listConversationPinnedMessages`
(`:832`), `exportConversationControlData` (`:838`), `runConversationControlAction` (`:844`).
Conversation-level: `pinConversation` (`:783`), `muteConversation` (`:854`),
`archiveConversation` (`:861`), `markConversationUnread` (`:868`),
`openDirectConversation(target)` (`:909`).

**Voice / calls:** the conversation model carries `voice_call` and a `voice` counter
(`:333`, `:356`); calls open the separate stack route `Call` (`callType: "audio" |
"video"`). **UNVERIFIED:** whether voice *messages* (as distinct from voice calls) have a
dedicated composer control — no voice-note-specific API function exists in
`api/messenger.ts`; attachments are the generic `attachment_ids` path.

**UNDX inside Chat:** `ChatScreen` renders UNDX action cards with an approval loop —
"UNDX action cards", "Confirm UNDX action", "Cancel UNDX action", "Undo UNDX action",
"UNDX action outcome", "Open the affected PulseSOC screen" — backed by
`sendPulseAiMessage` (`POST /api/pulse-ai/message`), `confirmPulseAiAction`,
`cancelPulseAiAction` (`api/messenger.ts:587-626`).

### 4.4 Profile — `src/screens/ProfileScreen.tsx` (831 lines)

Tab `Profile` (`AppNavigator.tsx:273`); other people open the stack route `ProfileDetail`.

**Own vs other user.** One screen, one `owner` boolean, driven by
`src/profile/profileContext.ts`. Data: `getMyProfile` / `getPublicProfile` /
`loadCachedProfile` / `listPublicProfilePosts` / `profileErrorState` from `api/profile.ts`
(`/api/pulse/profile/me`, `/api/pulse/profile/update`, avatar & cover upload/remove,
`/api/pulse/premium/profile-theme`). Identity resolution is centralised in
`api/profileTarget.ts` (`resolveProfileTarget`, `profileNavigationParams`,
`profileTargetFromAuthor`).

**Header** (`src/components/ProfileHeader.tsx`): cover + avatar, display name, handle,
verified / premium badges (`Badge`, `:469`), bio, and an action row (`Action`, `:495`).

**Stats** (`ProfileStatKey`, `:267-275`) — each is tappable:
| Stat | Source | Tap |
|---|---|---|
| Posts | `post_count` | switch to the Posts tab |
| Followers | `follower_count` | follower summary (`handleStat`, `ProfileScreen.tsx:352-356`) |
| Following | `following_count` | following summary |
| Media | `media_count` | switch to the Media tab |

Followers/Following are **hidden for non-owners when the profile hides them** — the array is conditional at `ProfileHeader.tsx:269-273`.

**Relationship / contact actions**
| Action | Result |
|---|---|
| Follow / Unfollow | `toggleProfileFollow` → `POST /api/pulse/follows/toggle` |
| Message | `openDirectConversation` → `navigate("Chat", {conversationId})` (`:323`) |
| Call (audio/video) | `navigate("Call", {conversationId, callType, direction:"outgoing"})` (`:346`) |
| Edit profile | → `ProfileEdit` (`:520`) |
| Customize | → `ProfileEdit` (`:521`) |
| Grow profile | → `GrowthCenter {contentType:"profile"}` (`:522`) |
| Safety | → `SafetyHub {section: visitor ? "reports" : "overview"}` (`:523`) |

**Content tabs** (`:545-547`): **Posts** · **Media** (`posts.filter(p => p.media?.length)`,
`:79`) · **About** (`AboutPanel` → Verification Center, Safety Hub, Seller/Store, `:549`).
Grid tiles are `ProfilePostGridTile` → `ProfilePostViewer` (`:555-562`, carries
`contentTab`). Post-level actions on the profile grid reuse the shared stores:
`setSaved` (`social/useSaveAction.ts`), `reactToPost`, `repostPost`, `deletePost`,
`pulsePostUrl`, all via `useSocialActionGuard`, with optimistic reaction counts
recomputed locally so the profile copy cannot disagree with the feed copy (`:406-428`).

**PROFILE OS — all 18 tiles.** Grid heading is `"Profile OS"` on your own profile and
`"{Name}'s Profile OS"` for a visitor (`ProfileHeader.tsx:195`). Destinations come from
`src/profile/profileOsTiles.ts`, which stores an **owner** route and a **visitor** route
*separately*; `visitor: null` means the tile is filtered out of a visitor's grid (a
backstop message names the tile if one slips through — `ProfileScreen.tsx:369-386`).

| # | key | Label | Icon | Owner destination | Visitor destination |
|---|---|---|---|---|---|
| 1 | `identity` | Pulse Identity | person-circle | `PulseIdentity` | `PulseIdentity` (scoped to the profile) |
| 2 | `media` | Media | images | Media tab | Media tab |
| 3 | `music` | Music | musical-notes | `Music` | — (hidden) |
| 4 | `trust` | Trust | shield-checkmark | `TrustCenter` | — |
| 5 | `safety` | Safety | lock-closed | `SafetyHub {section:"overview"}` | — |
| 6 | `pulse_dna` | Pulse DNA | pulse | `IntelligenceCenter {title:"Pulse DNA"}` | — |
| 7 | `achievements` | Achievements | trophy | `GrowthCenter {contentType:"profile"}` | — |
| 8 | `activity` | Activity | flash | `ActivityInbox` | — |
| 9 | `briefings` | Briefings | telescope | `BriefingsHub` | — |
| 10 | `collections` | Collections | albums | `Saved` | — |
| 11 | `communities` | Communities | people | `Tabs → Groups` | — |
| 12 | `marketplace` | Marketplace | storefront | `Tabs → Marketplace` | — |
| 13 | `events` | Events | calendar | `Events {mode:"events"}` | — |
| 14 | `business` | Business | briefcase | `BusinessOs` | **`BusinessBuyerPreview {sellerUserId}`** |
| 15 | `presence` | Presence | id-card (fixed brand teal + glow) | `Presence` | — |
| 16 | `memories` | Memories | time | `Tabs → Status` | — |
| 17 | `progress` | Progress | trending-up (violet) | `ProgressCenter` | — |
| 18 | `premium` | Premium | diamond (gold + glow) | `Premium` (fires `trackPremium("premium_tile_opened")` on tap, `:377`) | — |

Only **three** tiles have a visitor destination: `identity`, `media`, `business`.
Tile state (`ProfileModuleState`, badges/counts) is supplied per key by
`profile/useBriefingsTile.ts` and `profile/usePremiumTile.ts`.

**Other Profile drill-downs:** `ProfileEdit`, `VerificationCenter`, `SellerStore`,
`ProfilePostViewer`, `ProfileDetail` (from any author in the grid), plus `ContentCover`
(`components/covers/ContentCover.tsx`) for cover art.

## Section 5 — Deep linking

### 5.1 Prefixes and the platform ceiling

`src/navigation/linking.ts:36`
```
prefixes: ["pulsesoc://", "https://pulsesoc.com"]
```

**Confirmed against the shipped configuration:**
- iOS Associated Domains: `applinks:pulsesoc.com` only — `mobile-native/app.json:15-16` **and** `mobile-native/ios/PulseSoc/PulseSoc.entitlements:9`. One domain, no `www`, no staging.
- Published AASA: `services/native_app_links.py:32-33` serves exactly two path rules — `"/pulse/*"` and `"/search*"` — from `GET /.well-known/apple-app-site-association` (`bot.py:123669`).
- Android: `app.json` `intentFilters` autoVerify on `https://pulsesoc.com` with **`pathPrefix: "/pulse"` only** — Android is *narrower* than iOS: it does not claim `/search`.
- Custom scheme: `"scheme": "pulsesoc"` (`app.json:5`). `pulsesoc://` bypasses AASA entirely and can reach every path in the config.

**Therefore the ceiling for `https://` universal links into the shipped binary is:**

| Platform | Resolvable https paths |
|---|---|
| iOS | `https://pulsesoc.com/pulse/**` and `https://pulsesoc.com/search*` |
| Android | `https://pulsesoc.com/pulse/**` only |
| any (scheme) | `pulsesoc://<anything in §5.4>` |

Everything else in `linking.ts` — `dashboard/…`, `account/…`, `privacy-center`,
`trust-center`, `security`, `scam-shield/…`, `help`, `saved`, `notifications`,
`education/lesson/…` — **is unreachable as a web link** and only fires via `pulsesoc://`
or in-app navigation. The web rebuild should therefore serve real pages at those URLs and
not expect an app hand-off.

> **Also load-bearing: deep linking is disabled when signed out.**
> `App.tsx:417` — `linking={signedIn ? linking : undefined}`. A logged-out cold start
> discards the incoming URL entirely; there is no post-login resume of a pending link.
> The web must handle the signed-out case itself.

### 5.2 `getStateFromPath` — the custom resolution order

`linking.ts:37-59`. Before the static `config.screens` table is consulted, four checks run
**in this order**, and the first match wins:

1. `canonicalNativeRoute(path)` normalises the input — a `pulsesoc://host/path` URL is flattened to `/host/path`, slashes are collapsed, and query/hash are split out (`nativeRouteActions.ts:15-40`).
2. **Settings registry** — `settingsDeepLink()` (`:25-33`), regex `/^\/?settings\/([a-z0-9-]+)\/?$/i`. The id is resolved through `findSettingsEntry` in `src/settings/registry.ts`, so the link and the settings index row can never disagree. An **unknown id falls back to `Tabs → Settings`** rather than failing. The prefix is `settings/`, not `pulse/settings/`, deliberately — the latter is already claimed by `AccountCenter`'s `pulse/settings/:section`. *(Note: `settings/…` is not under `/pulse`, so this is a `pulsesoc://`-only family.)*
3. `/pulse/profile/edit` → `ProfileEdit` (`:42-44`).
4. `profileTargetFromUrl` → `ProfileDetail` (`:45-48`) — `src/api/profileTarget.ts` owns the handle/id parsing for any profile-shaped URL.
5. `nativeObjectDestination(path)` (`:49-57`) — the object regex table, below.
6. Otherwise `getStateFromPath` against `config.screens`.

### 5.3 The object regexes (`nativeRouteActions.ts:82-125`)

These are the canonical single-object URL shapes. All numeric ids use `[1-9]\d*` — a
leading zero or `0` does not match.

| Regex | Route | Params |
|---|---|---|
| `^/pulse/(?:post\|posts)/([1-9]\d*)/?$` | `PostDetail` | `postId` |
| `^/pulse/reels/([1-9]\d*)/?$` | `ReelDetail` | `reelId` |
| `^/pulse/status/([1-9]\d*)/?$` | `StatusDetail` | `statusId` |
| `^/pulse/live/([1-9]\d*)/?$` | `LiveDetail` | `liveId` |
| `^/pulse/marketplace/([1-9]\d*)/?$` | `MarketplaceDetail` | `listingId` |
| `^/pulse/messages/([1-9]\d*)/?$` | `Chat` | `conversationId` |
| `^/pulse/notifications/([1-9]\d*)/?$` | `NotificationCenter` | `notificationId` |
| `^/pulse/briefings/([1-9]\d*)/?$` | `BriefingDetail` | `briefingId` |
| `^/pulse/events/([1-9]\d*)/?$` | `EventDetail` | `eventId` |
| `^/pulse/(?:stores?\|business(?:es)?)/([^/]+)/?$` | `MerchantProfile` | `sellerId` (decoded) |
| `^/pulse/(?:ads?\|advertisements?)/([1-9]\d*)/?$` | `GrowthCenter` | `contentType:"advertisement"`, `contentId` |
| `^/pulse/(?:undx\|ai)/tasks/([^/]+)/?$` | `Tabs → PulseAI` | `taskId` |
| `^/pulse/calls/([^/]+)/?$` | `Call` | `callId`, `callType` from `?type=audio` else `video` |
| `^(?:/pulse)?/crypto/(?!alerts\|portfolio\|watchlist\|watchlists\|market\|search\|history\|assets\b)([A-Za-z0-9][A-Za-z0-9._-]{0,63})/?$` | `MarketPulse` | `openAsset` (token passed through untouched; server resolves symbol vs CoinGecko id) |

The crypto negative lookahead is what stops `/pulse/crypto/alerts` from resolving as a coin
named "alerts".

### 5.4 The static route table — 97 stack routes + 14 tab routes

`config.screens` declares **97 top-level stack routes** plus the `Tabs` group's **14 tab
paths** (`Create` has no path — it is the composer redirect). That is **111 of the 182
unique route names**; the remaining ~71 are **not deep-linkable at all** and can only be
pushed in-app.

**Tab paths** (`linking.ts:62-79`): `pulse/dashboard`, `pulse` (Home), `pulse/search`,
`pulse/saved`, `pulse/groups`, `pulse/live`, `pulse/reels`, `pulse/status`,
`pulse/messages`, `pulse/notifications`, `pulse/ai`, `pulse/profile`, `pulse/marketplace`,
`pulse/settings`.

**Universal-link-resolvable (`/pulse/*`, reachable from the web on both platforms):**

| Path | Route | Parsed params |
|---|---|---|
| `pulse` | Tabs→Home | |
| `pulse/dashboard` | Tabs→Dashboard | |
| `pulse/search` | Tabs→Search | |
| `pulse/saved` | Tabs→Saved | |
| `pulse/groups` | Tabs→Groups | |
| `pulse/groups/:groupSlug` | `GroupDetail` | |
| `pulse/live` | Tabs→Live | |
| `pulse/live/:liveId` | `LiveDetail` | `liveId:Number` |
| `pulse/live/schedule` | `LiveScheduleGateway` | |
| `pulse/live/events/create` | `LiveEventCreateGateway` | |
| `pulse/reels` | Tabs→Reels | |
| `pulse/reels/:reelId` | `ReelDetail` | `reelId:Number` |
| `pulse/status` | Tabs→Status | |
| `pulse/status/:statusId` | `StatusDetail` | `statusId:Number` |
| `pulse/post/:postId` | `PostDetail` | `postId:Number` |
| `pulse/messages` | Tabs→Messenger | |
| `pulse/messages/new` | `NewChat` | `initialQuery:String`, `targetUserId:Number` |
| `pulse/messages/:conversationId` | `Chat` | `conversationId:Number` |
| `pulse/calls/:callId?` | `Call` | `callId`, `conversationId:Number`, `callType`, `direction` |
| `pulse/notifications` | Tabs→Notifications | |
| `pulse/ai` | Tabs→PulseAI | |
| `pulse/profile` | Tabs→Profile | |
| `pulse/profile/edit` | `ProfileEdit` | |
| `pulse/profile/:profileKey` | `ProfileDetail` | |
| `pulse/presence` | `Presence` | |
| `pulse/pages` / `pulse/pages/create` / `pulse/pages/:handle` | `PagesHub` / `PageCreate` / `Page` | order matters — `create` declared first |
| `pulse/settings` | Tabs→Settings | |
| `pulse/settings/:section` | `AccountCenter` | `section` |
| `pulse/settings/devices` | `AccountDevices` | |
| `pulse/settings/notifications` | `NotificationPreferences` | |
| `pulse/marketplace` | Tabs→Marketplace | |
| `pulse/marketplace/create` | `MarketplaceCreateGateway` | |
| `pulse/marketplace/:listingId` | `MarketplaceDetail` | `listingId:Number` |
| `pulse/seller-store` | `SellerStore` | `mode`, `sellerId` |
| `pulse/orders` | `BuyerOrders` | `orderId:Number`, `source` |
| `pulse/orders/:orderId` | `BuyerOrderDetail` | `orderId:Number`, `source` |
| `pulse/purchases` | `BuyerPurchases` | |
| `pulse/merchant/apply` / `pulse/merchant/dashboard` / `pulse/merchant/:sellerId` | `MerchantApply` / `MerchantDashboard` / `MerchantProfile` | |
| `pulse/events` | `Events` | `eventId:Number`, `mode` |
| `pulse/events/:eventId` | `EventDetail` | `eventId:Number` |
| `pulse/compose` | `DashboardComposeAlias` | |
| `pulse/music` | `Music` | `trackId`/`track`:String, `artistId`/`artist`:Number, `openUpload`:Boolean |
| `pulse/music-alias` | `DashboardMusicAlias` | |
| `pulse/camera/:mode?` | `CameraStudio` | `mode`, `target`, `captureMode`, `conversationId:Number` |
| `pulse/premium` | `Premium` | |
| `pulse/creator-studio` / `pulse/creator` | `CreatorStudio` / `CreatorStudioAlias` | |
| `pulse/content-planner` | `ContentPlanner` | `mode` |
| `pulse/dashboard/content-planner-web` / `…/content-planner` | `ContentPlannerWeb` / `ContentPlannerPulseAlias` | `mode` |
| `pulse/dashboard/post-scheduler-web` / `…/post-scheduler` | `PostScheduler` / `PostSchedulerPulseAlias` | |
| `pulse/dashboard/draft-studio-web` / `…/draft-studio` | `DraftStudio` / `DraftStudioPulseAlias` | |
| `pulse/dashboard/module/:groupKey/:moduleKey` | `DashboardModuleDetail` | |
| `pulse/courses` | `Courses` | `category` |
| `pulse/courses/:courseId` | `CourseDetail` | `courseId:Number` |
| `pulse/teachers/:teacherId?` | `TeacherProfileGateway` | |
| `pulse/teacher-dashboard` | `TeacherDashboardGateway` | |
| `pulse/growth` | `GrowthCenter` | |
| `pulse/intelligence/:subsystem?` | `IntelligenceCenter` | |
| `pulse/undx/actions` | `UndxActionCenter` | `orgId`, `actor`, `productArea` |
| `pulse/alerts/:alertId?` | `AlertManagement` | `alertId:Number` |
| `pulse/crypto` | `MarketPulse` | `category` |
| `pulse/crypto/alerts` | `CryptoAlertManagement` | `alertId`/`alert_id`/`id`:Number |
| `pulse/portfolio` | `Portfolio` | |
| `pulse/watchlists` | `Watchlists` | |
| `pulse/private-office` + `/facts` `/security` `/documents` `/people` `/briefings` `/shield` `/concierge` | `PrivateOffice`, `PrivateFacts`, `PrivateOfficeSecurity`, `PrivateDocuments`, `PrivatePeople`, `PrivateBriefings`, `PrivateShield`, `PrivateConcierge` | literals declared before the pattern |
| `pulse/private-office/:view` | `PrivateOperations` | `view` — claims the six record views |
| `pulse/private-office/capital-graph` / `…/:id` | `CapitalGraph` / `CapitalEntity` | `id:Number` |
| `pulse/account-health` | `AccountHealth` | |
| `pulse/safety/:section?` | `SafetyHub` | `section` |
| `pulse/dashboard/network-safety/:section?` | `SafetyWebHub` | `section` |
| `pulse/help` | `TrustSafety` | `mode` |
| `pulse/support` | `TrustSafetySupport` | |
| `pulse/verification/:track?` | `VerificationCenter` | `track` |
| `pulse/dashboard/account-verification` | `VerificationWebCenter` | `track` |
| `pulse/activity/:category?` | `ActivityInbox` | `category` |
| `pulse/inbox` | `ActivityInboxLegacyInbox` | |

**iOS-only universal link** (matches `/search*`, not claimed by Android):
`search` → `Search` with `query:String`.

**Declared but NOT reachable via https** (custom scheme / in-app only):
`dashboard`, `dashboard/home`, `dashboard/orders`, `dashboard/:legacyGroup/:legacyModule/:legacySubmodule?`,
`dashboard/account/settings`, `dashboard/account/security`, `dashboard/account/health`,
`dashboard/activity`, `dashboard/inbox`, `account/settings`, `account/security`,
`privacy-center`, `trust-center`, `security`, `scam-shield/:mode?`, `help`, `saved`,
`notifications`, `education/lesson/:lessonSlug`, and the whole `settings/<id>` registry family.

### 5.5 What the website must match

1. Own `https://pulsesoc.com/pulse/*` as the canonical public URL space — that is the only namespace both app platforms claim.
2. Keep `/search` working as a web page; only iOS hands it to the app.
3. Object URLs must use the exact singular/plural spellings the regexes accept: `/pulse/post/:id` (or `posts`), `/pulse/reels/:id`, `/pulse/status/:id`, `/pulse/live/:id`, `/pulse/marketplace/:id`, `/pulse/messages/:id`, `/pulse/events/:id`, `/pulse/briefings/:id`, `/pulse/stores/:handle` (or `store`/`business`/`businesses`), `/pulse/ads/:id`, `/pulse/calls/:id`, `/pulse/undx/tasks/:id`, `/pulse/crypto/:asset`. Ids must be positive integers with no leading zero.
4. Serve real pages for `/terms`, `/privacy`, `/dashboard/*`, `/account/*`, `/privacy-center`, `/trust-center`, `/security`, `/scam-shield/*`, `/help`, `/saved`, `/notifications` — the app will never intercept these from a browser.
5. Ordering traps to mirror: `pulse/pages/create` before `pulse/pages/:handle`; the literal `pulse/private-office/*` paths before `pulse/private-office/:view`; `pulse/crypto/alerts` excluded from the coin pattern.

---

# Part II — Product area catalogue


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
