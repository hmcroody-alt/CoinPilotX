# PulseSoc Native QA Browser Report

Date: 2026-07-04

## Scope

This QA pass used the built-in QA browser/browser automation against the Expo web target for `mobile-native/`.

Rules followed:

- Did not use Chrome Incognito.
- Verified `localhost:8094` was listening before opening the browser.
- Did not repeatedly open dead localhost URLs.
- Did not touch production WebView paths.
- Preserved unrelated dirty work.
- Did not claim native device behavior as verified.

## Server Verification

Command:

```bash
cd mobile-native
npm run web:qa
```

Port check before browser navigation:

```bash
curl -I --max-time 10 http://localhost:8094
```

Result:

- `HTTP/1.1 200 OK`
- Expo web served `text/html`
- Metro reported `Waiting on http://localhost:8094`

## QA Browser Evidence

Screenshots captured from the built-in QA browser:

- `reports/screenshots/pulsesoc_native_qa_browser_login_20260704.png`
- `reports/screenshots/pulsesoc_native_qa_browser_login_mobile_20260704.png`

Responsive check:

- Viewport tested: `390x844`
- Login route rendered without horizontal overflow.
- Browser measurement: `scrollWidth=390`, `clientWidth=390`

## Issue Found And Fixed

### Duplicate Reels Linking Pattern

Initial browser boot failed before rendering the app.

Console error:

```text
Found conflicting screens with the same pattern. The pattern 'pulse/reels' resolves to both 'Tabs > Reels' and 'Reels'.
```

Root cause:

- `mobile-native/src/navigation/linking.ts` mapped `pulse/reels` to both:
  - `Tabs > Reels`
  - root stack `Reels`

Fix:

- Kept `pulse/reels` on the Reels tab.
- Removed the duplicate root stack `Reels` linking path.
- Preserved `pulse/reels/:reelId` for `ReelDetail`.
- Did not change backend routes or production WebView paths.

Retest:

- App booted to `http://localhost:8094/Login`.
- Login screen rendered.
- Signup navigation rendered.
- Direct feature routes safely redirected to the signed-out login gate instead of crashing.

## Auth And Navigation QA

Verified in the built-in QA browser:

- App boot: passed after the Reels linking fix.
- Login screen: passed.
- Login fields: rendered `Email or username` and `Password`.
- Signup navigation: passed.
- Signup screen: rendered `Create PulseSoc account`, `Full name`, `Username`, `Email`, and `Password`.
- Back navigation from Signup to Login: passed.

Not verified:

- Successful login, because no QA account/session was provided in this mission.
- Session restore after real login.
- Authenticated tab navigation.
- Authenticated API payload correctness.

## Feature Route Probe

While signed out, direct route probes safely landed on the auth gate:

| Area | Route | Browser Result |
| --- | --- | --- |
| Home Feed | `/pulse` | Redirected to Login |
| Messenger | `/pulse/messages` | Redirected to Login |
| Notifications | `/pulse/notifications` | Redirected to Login |
| Profile | `/pulse/profile` | Redirected to Login |
| Reels | `/pulse/reels` | Redirected to Login |
| Status | `/pulse/status` | Redirected to Login |
| Marketplace | `/pulse/marketplace` | Redirected to Login |
| Search | `/pulse/search` | Redirected to Login |
| Saved | `/pulse/saved` | Redirected to Login |
| Groups | `/pulse/groups` | Redirected to Login |
| Live | `/pulse/live` | Redirected to Login |
| Premium | `/pulse/premium` | Redirected to Login |
| Creator | `/pulse/creator-studio` | Redirected to Login |
| Growth | `/pulse/growth` | Redirected to Login |
| Intelligence | `/dashboard/intelligence` | Redirected to Login |

This verifies route safety while signed out. It does not verify authenticated feature behavior.

## Console And Network Findings

Console:

- Initial blocker was the duplicate Reels linking pattern.
- After the fix, the app rendered successfully.
- The browser log store retained stale pre-fix entries, so current runtime status was also checked through rendered state and Metro output.

Metro:

- Current output showed successful web bundles.
- Current output included `NO_COLOR`/`FORCE_COLOR` transform warnings only.

Network:

- Server availability was verified by `curl -I`.
- No authenticated feature API network calls were tested because the app remained signed out.

## Feature QA Status

| Feature | QA Browser Status | Notes |
| --- | --- | --- |
| App boot | Passed after fix | Login screen renders |
| Login screen | Passed | Fields and actions visible |
| Signup screen | Passed | Navigation works |
| Navigation tabs | Not verified | Requires signed-in session |
| Home Feed | Auth gate only | Feature body not verified |
| Messenger | Auth gate only | Feature body not verified |
| Notifications | Auth gate only | Feature body not verified |
| Profile | Auth gate only | Feature body not verified |
| Reels | Auth gate only | Duplicate route fixed; player not verified |
| Status | Auth gate only | Viewer/creator not verified |
| Marketplace | Auth gate only | Browse/detail not verified |
| Search | Auth gate only | Results not verified |
| Saved | Auth gate only | Collections not verified |
| Groups | Auth gate only | Group detail not verified |
| Live | Auth gate only | Playback/chat not verified |
| Premium | Auth gate only | Checkout/billing fallback not verified |
| Creator | Auth gate only | Dashboard not verified |
| Growth | Auth gate only | Dashboard not verified |
| Intelligence | Auth gate only | Alerts not verified |

## Native-Only Features Not Testable In Web QA

These still require simulator or real device QA:

- Push permissions and token registration.
- Camera.
- Microphone.
- Native file/document picker.
- Native image/video picker.
- Media upload from device storage.
- Reels native video performance.
- Status native video performance.
- Live playback reliability.
- Background/foreground recovery.
- Deep links into an installed app.
- Lock-screen/full-screen call behavior.

## Remaining QA Blockers

Highest priority blockers:

1. No provided QA login/session, so authenticated feature surfaces cannot be verified in browser QA.
2. iOS Simulator remains blocked until full Xcode/simctl is available.
3. Android Emulator/physical Android remains blocked until `adb` is available.
4. Push, camera, microphone, media upload, Live playback, and installed-app deep links remain device-only.

## Recommended Next Step

Create a dedicated native QA account or session fixture for browser/device testing, then rerun this QA browser mission against authenticated routes before adding another major feature.
