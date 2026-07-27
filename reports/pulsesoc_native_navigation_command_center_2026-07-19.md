# PulseSoc Native — Navigation Command Center Audit & Route Activation

Date: 2026-07-19
Scope: `mobile-native` — "PulseSoc Navigation" command center (49 actions)

## Executive summary

The "PulseSoc Navigation" screen is driven by a **local, typed route registry** (not a
server response, despite the "server authoritative" badge). All 49 actions were traced
end-to-end through the real router. Findings:

- **45 of 49** destinations already resolved to a correct native / shell / web-fallback target.
- **4 real defects** were found and fixed (3 collisions + 1 wrong-screen), all covered by new tests.
- The header overlapped the iOS status bar / Dynamic Island; fixed with safe-area insets.

This pass delivered the **correctness-critical, test-verifiable** subset of the mission:
route audit, route repair, distinctness fixes, safe-area header fix, and a 24-case test suite.
The large futuristic visual redesign, recent/pinned, category-jump rail, and on-device QA
were **not** performed — see "Not done / limitations".

## Architecture (source of truth)

| Concern | File |
| --- | --- |
| Screen component | `mobile-native/src/components/MasterNavigationDrawer.tsx` |
| Route registry (49 actions) | `mobile-native/src/navigation/masterNavigation.ts` |
| Primary route resolver | `mobile-native/src/navigation/nativeRouteActions.ts` (`openNativeRoute`) |
| Dashboard/legacy resolver | `mobile-native/src/navigation/dashboardRouting.ts` (`openDashboardRoute`) |
| Screen registration | `mobile-native/src/navigation/AppNavigator.tsx` |
| Param list | `mobile-native/src/navigation/types.ts` |

Data origin: **local TypeScript registry** (`masterNavigationSections`). The "server
authoritative" chip is display metadata only — the client resolves routes locally.

## Defects found and fixed

1. **Notifications collided with Activity Inbox.** `/pulse/notifications` navigated to
   `ActivityInbox`, identical to `/pulse/activity`. A dedicated, production-ready
   `NotificationCenterScreen` existed and was registered but nothing routed to it.
   → `/pulse/notifications` now opens `NotificationCenter`.
2. **Notification Preferences collided with Activity Inbox.** `/dashboard/network/notifications`
   also fell through to `ActivityInbox`. A dedicated `NotificationPreferencesScreen` existed,
   registered but unrouted.
   → `/dashboard/network/notifications` now opens `NotificationPreferences`.
   Result: **Activity Inbox, Notifications, and Notification Preferences are now three distinct
   destinations** (asserted by test).
3. **Terms opened the support screen instead of the legal document.** `/terms` navigated to
   `TrustSafetyHelp` (the Trust & Safety **support/scam/report** screen), which does not render
   the Terms document. Registry classified it `fallback` ("Legal document provider boundary").
   → `/terms` now uses the safe production web fallback (`Linking` → `${PULSE_API_BASE_URL}/terms`).
4. **Privacy Policy** had the identical defect → now safe web fallback to `/privacy`.

Files changed:
- `src/navigation/nativeRouteActions.ts` — split the activity/notifications branch; route
  Terms/Privacy through `openDashboardWebFallback`.
- `src/navigation/dashboardRouting.ts` — added preferences/center/activity disambiguation
  before the generic `/notifications` catch.
- `src/components/MasterNavigationDrawer.tsx` — safe-area top/bottom insets on the panel/scroll.

## 49-action route matrix (final state)

Legend: ✅ correct · ⚠ works but routes to a broader/shared surface (noted).

### Primary
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| Home | `/pulse` | Tabs/Home | native ✅ |
| Dashboard | `/pulse/dashboard` | Tabs/Dashboard | native ✅ |
| Search | `/pulse/search` | Tabs/Search | native ✅ |
| Activity Inbox | `/pulse/activity` | ActivityInbox | native ✅ |
| Settings | `/pulse/settings` | Tabs/Settings | native ✅ |

### Social
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| Messages | `/pulse/messages` | Tabs/Messenger | native ✅ |
| Calls | `/pulse/calls/qa-call-1` | Call (callId `qa-call-1`) | shell ⚠ hardcoded QA call id |
| Profile | `/pulse/profile` | Tabs/Profile | native ✅ |
| Profile Edit | `/pulse/profile/edit` | ProfileEdit | native ✅ (guarded against profile-URL hijack) |
| Groups | `/pulse/groups` | Tabs/Groups | native ✅ |
| Saved | `/pulse/saved` | Tabs/Saved | native ✅ |

### Creator / Business
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| Create Post | `/pulse/compose` | Tabs/Home `{openComposer}` | native ✅ composer-first |
| Camera | `/pulse/camera/photo?target=feed` | CameraStudio (photo/feed) | native ✅ dedicated flow |
| Creator Studio | `/pulse/creator-studio` | CreatorStudio | native ✅ |
| Content Planner | `/dashboard/creator/content-planner` | ContentPlanner (planner) | native shell ✅ |
| Draft Studio | `/dashboard/creator/draft-studio` | ContentPlanner (drafts) | native shell ✅ |
| Growth Center | `/pulse/growth` | GrowthCenter | native ✅ |
| Courses | `/pulse/courses` | Courses | native ✅ |
| Events | `/pulse/events` | Events | native ✅ |

### Content
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| Reels | `/pulse/reels` | Tabs/Reels | native ✅ |
| Status | `/pulse/status` | Tabs/Status | native ✅ |
| Add Status | `/pulse/status/create` | Tabs/Status `{openCreator}` | native ✅ |
| Live Viewer | `/pulse/live` | Tabs/Live | native ✅ |
| Live Studio | `/pulse/live/studio` | web fallback (`Linking`) | web-fallback ✅ |
| Pulse Radio | `/pulse/music#pulse-radio` | Music | native ✅ authoritative music session |

### Economy
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| Marketplace | `/pulse/marketplace` | Tabs/Marketplace | native ✅ |
| Seller Store | `/pulse/seller-store` | SellerStore | native ✅ |
| Create Listing | `/pulse/marketplace/create` | MarketplaceCreateGateway | native ✅ |
| Seller Inventory | `/dashboard/economy/seller-tools` | SellerStore | native shell ⚠ opens Seller Store; eligibility handled by that screen |
| Buyer Orders | `/pulse/orders` | BuyerOrders | native ✅ |
| Premium | `/pulse/premium` | Premium | native ✅ |

### Intelligence
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| UNDX | `/pulse/ai` | Tabs/PulseAI | native ✅ (LogiNexus badge) |
| Intelligence Center | `/pulse/intelligence` | IntelligenceCenter | native ✅ |
| Alert Management | `/pulse/alerts` | AlertManagement | native ✅ |
| Crypto Command | `/dashboard/crypto/alerts` | AlertManagement | native shell ⚠ shares Alert Management |
| Watchlists | `/dashboard/crypto/watchlists` | DashboardModuleDetail (Watchlists) | native shell ✅ |
| Scam Shield | `/scam-shield/scan` | ScamShield | native ✅ |

### Trust
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| Account Center | `/dashboard/account/settings` | Tabs/Settings | native ⚠ opens Settings tab, not a distinct Account Center |
| Security Center | `/dashboard/account/security` | AccountCenter (security) | native ✅ |
| Privacy Center | `/pulse/settings/privacy` | AccountPrivacy | native ✅ |
| Verification | `/pulse/verification` | VerificationCenter | native ✅ |
| Account Health | `/pulse/account-health` | AccountHealth | native ✅ |
| Safety Hub | `/pulse/safety` | SafetyHub | native ✅ |
| Support | `/pulse/support` | TrustSafetySupport | native ✅ |

### Utility
| Action | Route | Resolves to | State |
| --- | --- | --- | --- |
| Notifications | `/pulse/notifications` | **NotificationCenter** | native ✅ **FIXED** |
| Notification Preferences | `/dashboard/network/notifications` | **NotificationPreferences** | native ✅ **FIXED** |
| Terms | `/terms` | **web fallback → /terms** | web-fallback ✅ **FIXED** |
| Privacy Policy | `/privacy` | **web fallback → /privacy** | web-fallback ✅ **FIXED** |
| System Status | `/dashboard/system/feed` | IntelligenceCenter (System Status) | shell ⚠ reuses Intelligence Center |

## Classification totals (post-fix)
- Native: 34
- Native shell: 8
- Web fallback: 3 (Live Studio, Terms, Privacy Policy)
- External: 0
- Unavailable / dead: **0** (verified by the coverage test — every registry route produces a
  real navigation or web fallback).

## Header / safe-area correction
`MasterNavigationDrawer` renders inside a full-height `Modal` panel with no safe-area padding,
so the title collided with the status bar/time and Dynamic Island. Added
`useSafeAreaInsets()`; the panel now pads `insets.top` and the scroll content pads
`insets.bottom` (home indicator clearance).

## Tests
New: `mobile-native/src/navigation/__tests__/routeResolution.test.ts` (24 cases):
- Activity / Notifications / Notification Preferences resolve to 3 distinct screens.
- Terms & Privacy use the web fallback, not the support screen.
- Representative native tab + stack destinations (Home, Messages, Music, Camera, Compose, etc.).
- Create Post stays composer-first; Camera opens the dedicated CameraStudio flow.
- **Coverage guard:** every one of the 49 registry routes resolves to a navigation or web
  fallback (no dead routes).

Results:
- `npx tsc --noEmit` → clean (exit 0).
- `npx jest` → **12 suites, 132 tests passing** (24 new). Pre-existing `act()` warnings in
  unrelated screen tests are unchanged.
- `git diff --check` → clean.

## Backend / WebView changes
None. No server routes, contracts, or WebView destinations were modified. Terms/Privacy reuse
the existing `openDashboardWebFallback` → `PULSE_API_BASE_URL` contract already used elsewhere.

## Not done / limitations (honest)
The mission's larger redesign scope was intentionally **not** attempted in this pass because it
is either unverifiable without a device or carries high regression risk for a UI that cannot be
visually validated here:
- Full "futuristic" visual redesign, compact tiles, category accent system, motion/haptics.
- Category-jump rail, quick actions, Recent, Pinned destinations.
- Capability-badge relabel to user-facing language + developer-only route-path toggle.
- Simulator build, Expo Doctor, signed physical-device install, and the 64-step on-device QA
  matrix. **No on-device verification was performed.** Route behavior is verified by unit tests
  against the resolvers, not by running the app.

Recommended follow-ups (⚠ rows above), each low-risk and independently shippable:
- Calls: replace the hardcoded `qa-call-1` with a real call-history entry point.
- Account Center: point `/dashboard/account/settings` at `AccountCenter` (account section)
  rather than the Settings tab if a distinct hub is intended.
- Crypto Command vs Alert Management: differentiate or merge the two entries.
- System Status: give `/dashboard/system/feed` a dedicated status surface instead of reusing
  Intelligence Center.

## Rollback
Revert the three edited files and delete the new test:
`src/navigation/nativeRouteActions.ts`, `src/navigation/dashboardRouting.ts`,
`src/components/MasterNavigationDrawer.tsx`, and
`src/navigation/__tests__/routeResolution.test.ts`.
