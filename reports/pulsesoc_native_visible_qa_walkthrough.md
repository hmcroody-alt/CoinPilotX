# PulseSoc Native Visible QA Browser Walkthrough

Date: 2026-07-06

Scope: visible built-in QA browser walkthrough of the PulseSoc Native web QA build.

Result: passed for visual browser walkthrough coverage.

## QA Browser Setup

- Built-in QA browser used: yes.
- Chrome Incognito used: no.
- Production WebView routes changed: no.
- Android-specific testing: intentionally out of scope.
- Local same-origin QA proxy used: yes.
- Local API session used: yes, with runtime-only QA credentials that were not committed.

The visible walkthrough used a local same-origin QA stack so authenticated native screens could render in the browser without weakening production auth:

- Native web QA server: local Expo web build.
- Local QA backend: temporary SQLite-backed Flask process.
- Local proxy: same-origin `/api/*` forwarding to the temporary backend and all other routes forwarding to Expo web.

The browser login was performed through the visible Login UI, not by a hidden route check. The temporary credential was runtime-only and is not stored in this report.

## What Roody Saw Live

Roody saw the native app open in the built-in QA browser at `http://localhost:8094`, then watched an authenticated walkthrough across the implemented native surfaces.

Main navigation tabs were opened using the app UI:

- Login/Auth
- Home
- Search
- Saved
- Groups
- Live
- Reels
- Status
- Messenger
- Activity Inbox
- Pulse AI
- Profile
- Marketplace
- Settings

Deeper native surfaces were opened after the authenticated session was established:

- Calls
- Full-screen Incoming Calls fixture route
- Seller Store
- Seller Listing Composer
- Seller Inventory
- Buyer Orders
- Premium
- Creator Studio
- Growth Center
- Intelligence
- Alert Management
- Trust/Safety
- Verification Center
- Account Health
- Safety Hub
- Courses
- Camera Studio

## Walkthrough Coverage

- Visible signed-in screens checked: 30.
- Screens opened by app UI tab click: 13.
- Screens opened by authenticated deep route: 17.
- Auth gates during signed-in walkthrough: 0.
- Blank screens: 0.
- Navigation errors: 0.

Screenshots and the route-by-route result file were saved under:

`reports/screenshots/native-visible-qa-2026-07-06/`

Representative screenshots:

- `home.png`
- `messenger.png`
- `activity-inbox.png`
- `profile.png`
- `marketplace.png`
- `seller-store.png`
- `buyer-orders.png`
- `creator-studio.png`
- `intelligence.png`
- `verification.png`
- `account-health.png`
- `courses.png`
- `camera-studio.png`

## Visible Findings

The native app now presents a broad, signed-in PulseSoc shell through the QA browser. Home, Profile, Marketplace, Seller Store, Buyer Orders, Activity Inbox, Trust/Safety, Verification, Account Health, Courses, Creator, Growth, Intelligence, and Camera Studio all rendered as native surfaces or safe native fallbacks.

The walkthrough confirmed that the prior unacceptable state, where most signed-in surfaces were only auth-gated route checks, has been corrected for local QA browser review.

## Console / Platform Notes

Non-blocking browser console warnings observed:

- `expo-av` deprecation warning. This should be migrated later to current Expo audio/video modules.
- `expo-notifications` web support warning for push-token listener behavior.
- React Native web style warnings for deprecated shadow props in favor of `boxShadow`.

No browser console error blocked the walkthrough.

## Still Blocked Or Not Verified

The following remain release-readiness gaps, not browser-walkthrough blockers:

- Physical APNs/FCM delivery and lock-screen notification behavior.
- Real camera and microphone capture in Camera Studio.
- Native installed-app deep links.
- Real LiveKit two-device media calls.
- Production-scale data volumes and production event pressure.
- Real payment provider checkout completion on physical devices.
- Reels, Status, Messenger, and some commerce surfaces need richer seeded or staging data for visual depth checks.

## Production Safety

- No production WebView routes were modified.
- No production auth logic was weakened.
- No credentials were committed.
- The QA account/session was local and runtime-only.

## Production-Ready Assessment

The native app is now visually demonstrable in the built-in QA browser across the major implemented surfaces. It is not production-replacement ready until physical iPhone QA, provider push QA, payment provider QA, and production-scale event sync validation are complete.

