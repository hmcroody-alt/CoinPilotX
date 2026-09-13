# PulseSoc Native App — Navigation & Primary Tabs

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
