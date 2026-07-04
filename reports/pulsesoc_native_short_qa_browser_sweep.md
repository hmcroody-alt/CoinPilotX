# PulseSoc Native Short Authenticated QA Browser Sweep

Date: 2026-07-04

## Scope

This was a short authenticated QA browser sweep for the parallel `mobile-native/` app. No new major feature was built.

The sweep used the built-in QA browser only. It did not use Chrome Incognito or any external browser. The production WebView paths were not modified.

## Environment

- Native web build: `npm run web:qa`
- QA URL: `http://localhost:8094`
- API base URL: local CORS proxy forwarding to a temporary local PulseSoc backend
- Backend health: `GET http://127.0.0.1:5107/health` returned ok
- Web server check: `curl -I http://localhost:8094` returned `HTTP/1.1 200 OK`
- Account: temporary local QA account, not a production account
- Credentials: not recorded in this report

## Browser Evidence

Screenshots captured through the built-in QA browser:

- `reports/screenshots/pulsesoc_native_short_qa_home_20260704.png`
- `reports/screenshots/pulsesoc_native_short_qa_intelligence_20260704.png`
- `reports/screenshots/pulsesoc_native_short_qa_settings_20260704.png`

## Auth Flow Results

| Flow | Result | Notes |
| --- | --- | --- |
| Login | Verified | Login through the local QA account reached `/pulse` and rendered Home Feed. |
| Session restore | Verified | Direct navigation to `/pulse` restored the authenticated session and rendered Home Feed. |
| Logout | Verified after fix | Settings logout returned to `/Login`. |
| Re-login after logout | Verified after fix | Login controls were accessible through semantic labels/roles after the scoped accessibility patch. |

## QA Blocker Fixed

The built-in QA browser found that Login and Settings interactions worked visually but were exposed as generic web `div` controls on React Native Web. That made semantic QA locators unable to click `Sign out` or fill login fields by label after logout.

Scoped fix:

- Added `accessibilityLabel` to Login email and password inputs.
- Added `accessibilityRole="button"` to Login and Settings `Pressable` controls.

This is a native-web accessibility and QA hardening fix only. It does not change backend auth/session behavior or production WebView routes.

## Route Sweep Results

| Area | URL | Result | Notes |
| --- | --- | --- | --- |
| Home Feed | `/pulse` | Pass | Empty feed state rendered. |
| Messenger | `/pulse/messages` | Pass | Empty conversation state rendered. |
| Notifications | `/pulse/notifications` | Pass | Empty notification state rendered. |
| Profile | `/pulse/profile` | Pass | QA profile rendered with premium/theme metadata. |
| Reels | `/pulse/reels` | Pass | Empty Reels state rendered. |
| Status | `/pulse/status` | Pass | Status rail and create entry rendered. |
| Marketplace | `/pulse/marketplace` | Pass | Empty listings state rendered. |
| Search | `/pulse/search` | Pass | Search and discovery tabs rendered. |
| Saved | `/pulse/saved` | Pass | Saved library and collection filters rendered. |
| Groups | `/pulse/groups` | Pass | Communities and default rooms rendered after API-backed loading completed. |
| Live Viewer | `/pulse/live` | Pass | Live discovery, empty live-now state, scheduled area, and Go Live web fallback rendered. |
| Premium | `/pulse/premium` | Pass | Server-authoritative premium status rendered. |
| Creator Studio | `/pulse/creator-studio` | Pass | Creator dashboard rendered after API-backed loading completed. |
| Growth Center | `/pulse/growth` | Pass | Growth dashboard rendered after API-backed loading completed. |
| Intelligence/Alerts | `/dashboard/intelligence` | Pass | Intelligence Center rendered after API-backed loading completed. |
| Settings | `/pulse/settings` | Pass | Settings and session controls rendered. |
| Pulse AI | `/pulse/ai` | Pass | Pulse AI native chat shell rendered. |
| Notification Preferences | `/pulse/settings/notifications` | Pass | Notification preferences rendered. |
| Crypto Alert Deep Link | `/dashboard/crypto/alerts?alert_id=1` | Pass with limitation | Current native routing redirected to Home. This is safe, but it confirms alert-specific native routing remains unfinished. |
| Unsupported Camera Route | `/pulse/camera` | Pass with fallback | Unsupported native route safely redirected to Home instead of crashing. |

## Console And Network

Current sweep window after `2026-07-04T18:43:26Z` showed no new QA browser console warnings or errors.

The browser log store still contained older duplicate `/pulse/reels` route errors from the earlier QA-browser readiness pass. Those were stale entries from before the current sweep and did not recur during this run.

No current network crash or auth loop was observed in the built-in QA browser. API-backed empty states rendered where the temporary QA backend had no seed data.

## Layout And PulseSoc Identity

No critical layout break was found in the short sweep. The app preserved the PulseSoc-native direction: dark premium surface, compact navigation, server-owned cards, and safe fallback behavior.

Design note: many empty states are functional but sparse because the temporary QA database has little seeded content. A richer QA fixture is still needed to judge immersive PulseSoc polish across Feed, Messenger, Reels, Status, Marketplace, Groups, Live, Growth, and Intelligence.

## Device-Only Items Not Verified

These are still not verified by the browser sweep:

- APNs/FCM delivery
- Expo push token delivery on real device
- Notification sounds, lock-screen presentation, and installed-app tap handling
- Camera permissions
- Microphone permissions
- Gallery/file picker permissions
- Native media compression
- Native video playback performance
- Reels/Status gesture performance on device
- Live playback behavior on device
- Background/foreground recovery on iOS/Android
- Installed deep links
- Bluetooth/speaker/mic behavior for future calls

Do not treat this report as simulator-verified or physical-device-verified.

## Recommendation

Native Alert Management + Crypto/Market Alert CRUD remains the correct next feature after this sweep.

Reasons:

- The current authenticated native browser surfaces are stable enough to support another browser-verifiable feature.
- Intelligence/Alerts is already present, but alert create/edit/history/test flows still rely on web fallback.
- Production already exposes server-authoritative alert APIs and business rules.
- Native already has adjacent Notification Center, Notification Preferences, Premium, Growth, Intelligence, cache, routing, and safe fallback infrastructure.
- Alert CRUD can be substantially verified through the built-in QA browser before device push delivery is available.
- Camera, advanced media editor, Live hosting, and LiveKit calls remain more device-sensitive and should wait until real device QA is unblocked.

## Required Next QA Fixture

Before building Alert CRUD, use or create a safe local QA fixture with:

- At least one active crypto alert.
- At least one paused alert.
- At least one alert event/history row.
- Channel-readiness coverage for in-app, push, email, SMS, and Telegram.
- Premium and non-premium entitlement cases.

