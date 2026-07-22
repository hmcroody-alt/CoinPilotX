# PulseSoc Native WebView Replacement Readiness

Date: 2026-07-19

## Executive Decision

Native PulseSoc is **not ready** to replace the production WebView app once and for all under the new rule:

> No web redirected links. Everything should stay native.

The native app has broad coverage, but it still contains explicit web exits and safe-web-fallback paths. Those must be removed, replaced by native screens, or converted into honest native provider boundaries before the production App Store switch.

## Evidence

Audit script:

- `scripts/pulsesoc_native_webview_replacement_audit.py`

Machine-readable evidence:

- `reports/pulsesoc_native_webview_replacement_readiness.json`

Latest audit result:

- Release readiness: **FAIL**
- Native routes discovered: **96**
- Native screen files discovered: **45**
- Critical surface groups audited: **26**
- Critical surface groups with static native coverage and no direct web-exit finding: **13 / 26**
- Hard web-exit blockers: **63**

Blocker counts:

- `Linking.openURL`: **31**
- `safe_web_fallback`: **4**
- drawer entries marked `fallback`: **3**
- visible web-fallback copy: **25**
- `webPath` compatibility fields: **15**
- mounted React Native WebView components: **0**

Important distinction: the audit found no mounted `react-native-webview` component in `mobile-native/src`. The blocking issue is not a hidden native WebView wrapper. The blocking issue is remaining route policies and actions that still open production web URLs or describe web fallback behavior.

## Production Coverage Compared To Native

Production route/source references remain concentrated around:

- `/dashboard/`: **396** source mentions
- `/pulse/live`: **49**
- `/pulse/messages`: **43**
- `/pulse/notifications`: **31**
- `/pulse/premium`: **25**
- `/pulse/intelligence`: **17**
- `/pulse/music`: **16**
- `/pulse/creator`: **15**
- `/pulse/reels`: **15**
- `/pulse/profile`: **12**
- `/pulse/status`: **12**
- `/pulse/settings`: **11**
- `/pulse/camera`: **7**
- `/pulse/videos`: **6**
- `/pulse/search`: **4**
- `/pulse/groups`: **2**
- `/pulse/marketplace`: **2**
- `/pulse/dashboard`: **1**

Native has many route equivalents in `mobile-native/src/navigation/AppNavigator.tsx`, but route existence does not equal replacement readiness when the surface still opens web URLs or labels advanced work as fallback.

## Static Native Coverage Without Web-Exit Findings

These surface groups currently have static native route/screen coverage and no direct web-exit finding in their primary screen files:

- Home feed
- Messenger inbox
- Conversation
- Calls
- Groups / Rooms
- Reels
- Status
- Music / Pulse Radio
- Activity / Notifications
- Buyer Orders
- Premium / Billing primary screen
- Trust / Safety primary screens
- Saved

These are not automatically “release complete.” They still require simulator, physical-device, backend, and cross-client QA. They are simply not blocked by direct web-exit findings in this static gate.

## Blocked Or Incomplete Under Native-Only Rule

The following surface groups still contain direct web-exit or fallback findings:

- Authentication: migration copy still references WebView account/session compatibility.
- Search / Discover: visible copy says tabs remain on backend/web fallback until native destinations exist.
- Live: playback error copy directs users to web fallback.
- Camera Studio: mode destinations retain `webPath` compatibility and advanced camera fallback opens URL.
- Profile: profile fallback buttons still open web profile URLs.
- Marketplace: listing detail and checkout paths still open web/provider URLs.
- Seller / Store: dashboard/apply/create/payout paths still open web seller URLs.
- Creator Studio: content planner/growth APIs or screens still expose fallback behavior.
- Courses / Learning: advanced course/payment/review operations are explicitly described as safe fallback.
- Events: creation/payment gateway copy describes safe fallback.
- Dashboard: legacy dashboard routing still includes `safe_web_fallback` and opens URL fallback.
- Intelligence / UNDX: intelligence API contains open-web path.
- Account / Settings: account/support/verification/account-health APIs still have URL-opening helpers.

## Exact High-Risk Files

Navigation and route policy blockers:

- `mobile-native/src/navigation/dashboardRouting.ts`
- `mobile-native/src/navigation/masterNavigation.ts`
- `mobile-native/src/navigation/notificationRouting.ts`

Screen-level web exits or fallback copy:

- `mobile-native/src/screens/SearchScreen.tsx`
- `mobile-native/src/screens/ProfileScreen.tsx`
- `mobile-native/src/screens/MarketplaceScreen.tsx`
- `mobile-native/src/screens/SellerListingComposerScreen.tsx`
- `mobile-native/src/screens/SellerStoreScreen.tsx`
- `mobile-native/src/screens/CameraStudioScreen.tsx`
- `mobile-native/src/screens/LiveScreen.tsx`
- `mobile-native/src/screens/CoursesLearningScreen.tsx`
- `mobile-native/src/screens/EventsScreen.tsx`
- `mobile-native/src/screens/UserDashboardScreen.tsx`
- `mobile-native/src/screens/ContentPlannerScreen.tsx`
- `mobile-native/src/screens/AlertManagementScreen.tsx`
- `mobile-native/src/screens/DashboardModuleDetailScreen.tsx`

API/helper web exits:

- `mobile-native/src/api/account.ts`
- `mobile-native/src/api/accountHealth.ts`
- `mobile-native/src/api/calls.ts`
- `mobile-native/src/api/creator.ts`
- `mobile-native/src/api/events.ts`
- `mobile-native/src/api/growth.ts`
- `mobile-native/src/api/intelligence.ts`
- `mobile-native/src/api/learning.ts`
- `mobile-native/src/api/live.ts`
- `mobile-native/src/api/marketplace.ts`
- `mobile-native/src/api/orders.ts`
- `mobile-native/src/api/premium.ts`
- `mobile-native/src/api/safety.ts`
- `mobile-native/src/api/support.ts`
- `mobile-native/src/api/verification.ts`

## What This Means For The App Store Switch

Native PulseSoc can continue as a side-by-side development build, but it should **not** replace the WebView app yet if the release rule is “everything stays native.”

Current native replacement judgment:

- Home: close to release-ready based on prior reports and simulator evidence.
- Messenger/Pulse Command: partially native and improving, but exact parity and nested QA remain active work.
- Core navigation: broadly native, but fallback route policy remains a blocker.
- Commerce, seller, dashboard, creator, live studio, learning, and provider-heavy flows: not yet native-only.
- Legal/provider/payment boundaries: need product/legal/provider decisions. Some may require native SDKs or in-app purchase/provider-native flows rather than URL redirects.

## Required Native-Only Remediation Sequence

1. **Route policy hardening**
   - Remove `safe_web_fallback` as an executable route kind.
   - Replace fallback execution with native route resolution or a native “not yet available” boundary that does not open web.
   - Stop notification routing from opening unknown web targets.

2. **Dashboard native-only conversion**
   - Convert legacy dashboard actions to native module shells or dedicated native modules.
   - Any unknown dashboard route must show a native boundary, not open a URL.

3. **Profile and Search exits**
   - Remove profile “Open web fallback” actions.
   - Replace Search tab fallback copy/routes with native results or native unavailable states.

4. **Commerce and seller flows**
   - Convert marketplace listing detail, seller dashboard/apply/create/payouts, buyer/seller handoffs, and checkout/onboarding into native screens or approved provider-native SDK boundaries.

5. **Camera, Live Studio, Events, Courses**
   - Replace advanced camera/live/events/course fallbacks with native flows, or native provider boundaries that do not leave the app.

6. **Account, verification, safety, support, intelligence helpers**
   - Remove API helper methods whose only behavior is `Linking.openURL`.
   - Rewire callers to native stack routes or server-backed native forms.

7. **Final simulator and physical-device matrix**
   - Re-run the native-only audit.
   - Open every critical surface in Xcode iPhone Simulator.
   - Verify physical-device-only areas: camera, microphone, call audio, Bluetooth, background calls, push delivery, payment/provider handoffs, large media upload.

## Current Migration Estimate

Evidence-based estimate from this static audit and existing progress reports:

- Overall native migration: **~82%**
- Overall production UI parity: **~72%**
- Native-only route readiness: **~50%** clean critical surface groups by static gate
- Release QA confidence for replacing WebView today: **NO-GO**

The exact percentage can only rise after the hard web exits are removed and simulator/physical QA is refreshed. The current audit is intentionally strict because the stated requirement is no web redirected links.

## Next Highest-Value Mission

**Native Web Redirect Elimination Phase 1: Navigation, dashboard, notification, profile, and search.**

Reason:

- These are shared escape hatches that can redirect users out of native from many places.
- Fixing the route policy first prevents every later screen from reintroducing web fallback.
- It creates the enforcement gate required for the rest of the App Store replacement work.

## Release Answer

Can Native PulseSoc replace the production WebView app once and for all today?

**NO.**

True blockers:

- 63 hard web-exit or fallback findings remain in the native source.
- 13 of 26 critical surface groups are blocked or incomplete under the native-only static gate.
- Dashboard legacy routing can still open web fallback.
- Notification routing can still open web targets.
- Commerce/seller/profile/search/camera/live/course/event flows still contain web exits or fallback states.
- Physical-device release checks remain incomplete for camera, microphone, real calls, push, background behavior, Bluetooth/audio routing, and provider/payment flows.

## Wave 0 + Wave 1 Update — 2026-07-21

- Verdict unchanged: **NO-GO**. WebView-exit replacement was explicitly out of scope for the 2026-07-21 Wave 0/Wave 1 mission (release-gate cleanup + auth/session + Home layout), so remaining web-fallback source was not modified.
- Latest audit re-run (`scripts/pulsesoc_native_webview_replacement_audit.py`) still exits 1 with `release_readiness: FAIL` and `hard_blocker_count: 54` (see the machine-readable JSON); example still-active source: `mobile-native/src/screens/SearchScreen.tsx` events/lessons gateway fallback copy.
- What did change this mission (does not affect this verdict): the foundation/live/feature-parity release-gate audits were repaired to fail only on real active blockers (not weakened), and P0 auth/session (NRB-059) plus the Home bottom-dock overlap (NRB-058) were stabilized in code. Details: `reports/pulsesoc_native_wave0_wave1_auth_home_stabilization_2026-07-20.md`.
