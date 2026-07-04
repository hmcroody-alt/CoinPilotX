# PulseSoc Native Authenticated QA Browser Report

Date: 2026-07-04

## Scope

This pass used the built-in QA browser against the Expo web target for `mobile-native/`.

Rules followed:

- Did not use Chrome Incognito.
- Verified `localhost:8094` with `curl` before browser navigation.
- Used a local temporary QA backend and CORS proxy for authenticated browser testing.
- Did not use production PulseSoc credentials.
- Did not touch production WebView paths.
- Fixed only blockers found during QA.
- Did not claim real-device behavior as verified.

## Local QA Environment

Expo web:

```bash
cd mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=http://localhost:5108 npm run web:qa
```

Availability check before browser navigation:

```bash
curl -I --max-time 5 http://localhost:8094
```

Result:

- `HTTP/1.1 200 OK`
- Expo web served the native app at `http://localhost:8094`

Local QA API:

- Temporary local Flask backend on `127.0.0.1:5107`
- Temporary local QA CORS proxy on `localhost:5108`
- Temporary local QA account existed only in the local QA database
- The temporary account password is not committed in this report

API checks:

- `GET http://localhost:5108/health` returned `{"ok":true,"service":"coinpilotx-web"}`
- `GET http://localhost:5108/api/mobile/auth/session` returned a safe signed-out session before browser login

## Screenshots

Screenshots captured from the built-in QA browser:

- `reports/screenshots/pulsesoc_native_authenticated_qa_home_20260704.png`
- `reports/screenshots/pulsesoc_native_authenticated_qa_profile_20260704.png`
- `reports/screenshots/pulsesoc_native_authenticated_qa_intelligence_20260704.png`
- `reports/screenshots/pulsesoc_native_authenticated_qa_settings_20260704.png`

## Auth QA

| Flow | Result | Evidence |
| --- | --- | --- |
| Login | Passed | Browser login redirected to authenticated `/pulse` |
| Session restore | Passed | Direct reload of `/pulse` rendered authenticated Home Feed |
| Logout | Passed | Settings had exactly one `Sign out` control and returned to `/Login` |

Logout result:

- URL: `http://localhost:8094/Login`
- Title: `Login`
- Visible state: `PulseSoc`, `Sign in`, `Create account`

## Blockers Found And Fixed

### Web Session Storage Was Native-Only

Root cause:

- `mobile-native/src/session/sessionStore.ts` used `expo-secure-store` for all platforms.
- Expo web could not persist the native session cookie in the QA browser.

Fix:

- Native platforms still use `expo-secure-store`.
- Web uses `@react-native-async-storage/async-storage`.
- This preserves the native security path while allowing QA browser session restore.

### Browser Cookie Header Was Forbidden

Root cause:

- `mobile-native/src/api/pulseApi.ts` manually set the `Cookie` header when a session cookie existed.
- Browsers reject manual `Cookie` headers.

Fix:

- Native platforms still attach the stored cookie header.
- Web relies on `credentials: "include"` and browser-managed cookies.

### Settings Deep Link Fell Back To Home

Root cause:

- The Settings tab existed in `AppNavigator`, but `mobile-native/src/navigation/linking.ts` did not map it.
- Direct route `/Settings` fell back to Home during browser QA.

Fix:

- Added stable native routes:
  - `pulse/settings`
  - `pulse/ai`

Retest:

- `http://localhost:8094/pulse/settings` rendered Settings.
- `http://localhost:8094/pulse/ai` rendered Pulse AI.

### Intelligence Cards Could Crash On Object Payloads

Root cause:

- The Intelligence normalizer accepted array-shaped `cards`.
- The existing backend can return object-shaped card maps.
- Browser QA observed `cards.map is not a function`.

Fix:

- `normalizeIntelligenceCards(...)` now accepts arrays and object maps.
- The native client still uses the server-owned payload and does not duplicate Intelligence business logic.

Retest:

- `http://localhost:8094/dashboard/intelligence` rendered `Intelligence Center`.
- The runtime crash text was no longer visible.

## Authenticated Route Sweep

All routes below were tested in the built-in QA browser after login.

| Area | Route | Result |
| --- | --- | --- |
| Home Feed | `/pulse` | Passed; authenticated Home Feed rendered empty state |
| Messenger | `/pulse/messages` | Passed; Messenger rendered empty conversation state |
| Notifications | `/pulse/notifications` | Passed; notification center rendered all-caught-up state |
| Profile | `/pulse/profile` | Passed; QA profile rendered `Native Auth QA` |
| Reels | `/pulse/reels` | Passed; Reels rendered empty state |
| Status | `/pulse/status` | Passed; Status rail/viewer rendered empty state |
| Marketplace | `/pulse/marketplace` | Passed; Marketplace rendered empty state |
| Search | `/pulse/search` | Passed; search tabs and suggestions rendered |
| Saved | `/pulse/saved` | Passed; Saved route rendered loading/empty state during sweep |
| Groups | `/pulse/groups` | Passed; Communities and Rooms rendered |
| Live | `/pulse/live` | Passed; Live discovery rendered no-live state |
| Premium | `/pulse/premium` | Passed; Premium/entitlement screen rendered active local fixture state |
| Creator | `/pulse/creator-studio` | Passed; Creator Studio rendered dashboard summary |
| Growth | `/pulse/growth` | Passed; Growth Center rendered dashboard summary |
| Intelligence | `/dashboard/intelligence` | Passed after normalizer fix |
| Settings | `/pulse/settings` | Passed after linking fix |
| Pulse AI | `/pulse/ai` | Passed after linking fix |

## Console And Network Findings

Console:

- The browser log store still contained stale duplicate Reels linking errors from `2026-07-04T18:04:09.933Z`.
- Those entries were from the earlier pre-fix QA pass and did not recur during this authenticated route sweep.
- The current route sweep rendered the tested screens without visible runtime error text.

Network:

- Local web and local QA API were verified before browser navigation.
- Authenticated feature calls used the local temporary QA API/proxy.
- Production backend and production WebView routes were not modified.

## Deep-Link Routing

Verified:

- Authenticated top-level route deep links for Feed, Messenger, Notifications, Profile, Reels, Status, Marketplace, Search, Saved, Groups, Live, Premium, Creator, Growth, Intelligence, Settings, and Pulse AI.

Not verified:

- Installed-app deep links such as `pulsesoc://...`
- Detail deep links requiring real production records or seeded media records
- Push notification tap routing into an installed app

## Native-Only Behavior Not Verified

These still require simulator or real device QA:

- Push permission prompts and Expo push token registration.
- Camera permission and capture.
- Microphone permission and voice recording.
- Native image/video/file picker behavior.
- Device media upload from local storage.
- Reels and Status native video performance.
- Live playback and foreground/background recovery on device.
- Installed-app deep links and notification tap routing.

## Remaining Risks

- This was authenticated browser QA, not real-device QA.
- The local temporary QA backend did not contain realistic feed, message, reel, status, marketplace, saved, alert, or live media data.
- Empty-state rendering is verified for many surfaces, but rich-data rendering still needs seeded QA fixtures or production-safe test accounts.
- Push, device permissions, background recovery, native video, and upload behavior remain device-only.

## Recommended Next Action

Keep the project in QA-driven development mode.

Next priority:

1. Create or seed a durable QA fixture set with posts, messages, reels, statuses, marketplace listings, saved items, alerts, and live records.
2. Rerun authenticated browser QA against non-empty data.
3. Complete simulator or physical-device setup for push, media upload, native video, deep links, and background recovery.
