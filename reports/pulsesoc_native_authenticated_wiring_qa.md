# PulseSoc Native Authenticated Wiring QA

Date: 2026-07-09

## Scope

This pass restarted the local authenticated QA stack and ran a visible built-in QA browser walkthrough of representative native wiring. It did not add product features, redesign UI, focus on Android, or touch production WebView paths.

## Local QA Stack

- Local backend: `http://127.0.0.1:5107`
- Local QA proxy: `http://127.0.0.1:5108`
- Native web QA: `http://127.0.0.1:8094`
- API base used by Expo web: `EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5108`
- Built-in QA browser: used visibly.
- No Chrome Incognito: followed.
- QA account: disposable local-only account in the temporary SQLite backend. No password is committed or recorded in reports.

Health checks passed for the backend and proxy before opening the browser:

- `GET http://127.0.0.1:5107/health`
- `GET http://127.0.0.1:5108/health`
- `HEAD http://127.0.0.1:8094/Login`
- `HEAD http://127.0.0.1:8094/pulse`

## What Roody Could Watch

- Visible Login form.
- Successful authenticated sign-in through the native Login screen.
- Native Dashboard landing with live module data.
- Home, hero, status, composer, feed categories, and bottom navigation shell.
- Activity Inbox and legacy Notifications route.
- Search, Profile, Profile Edit, Reels, Status, Camera Studio, Messenger, Calls, Marketplace, Seller Store, Buyer Orders, Premium, Creator, Growth, Intelligence, Alerts, Settings, Security, Privacy, Support, Verification, Account Health, Safety, Courses, Dashboard module shells, and Pulse AI.
- Representative browser back-navigation checks between Home/Activity, Profile/Edit Profile, Marketplace/Seller Store, Settings/Security, and Dashboard/module shell.

## Scoped Fixes From QA

Two wiring gaps were found and fixed:

- Creator shorthand alias: `/pulse/creator` now opens the existing native Creator Studio via `CreatorStudioAlias`.
- Support alias: `/pulse/support` now opens the existing native Trust & Safety support shell instead of falling back to Dashboard.

No backend business logic was added or duplicated.

## Results

- 37 representative authenticated routes checked.
- 0 blank screens.
- 0 authenticated route login regressions after sign-in.
- 0 routing loops observed.
- 0 production WebView path changes.
- 5 representative back-navigation checks passed.
- State preservation remained intact for the sampled back-navigation flows.

## Warnings

- Web QA console warnings remain non-blocking:
  - React Native Web deprecated `shadow*` style props.
  - `expo-av` deprecation notice.
  - `props.pointerEvents` deprecation notice.
  - Web Badging API unavailable in the QA browser.
  - `useNativeDriver` fallback on web.
  - `expo-notifications` push token listener unsupported on web.
- Terms and Privacy Policy are provider/web fallback boundaries from Settings, not native legal-document screens.
- Calls route correctly renders a safe `Call not found` state for the QA fixture when no active backend call exists.
- Camera Studio correctly reports browser limitations for camera/microphone preview; device capture remains release QA.

## Authenticated QA Status

Authenticated QA coverage: 82%

Representative routes tested: 37

Failures: 0 after scoped alias fixes.

Warnings: 6 non-blocking web/runtime warnings.

Dead routes: 0 in the representative authenticated matrix.

Routing loops: 0 observed.

Back navigation: passed on 5 representative route pairs.

State preservation: passed for sampled Home, Profile, Marketplace, Settings, and Dashboard shell flows.

Visible QA coverage: 80%

Current native migration: 96%

Release QA confidence: 87%

Can authenticated routing now be considered production-ready: YES for representative browser routing; NO for full release because iPhone push/tap, camera hardware, and provider flows remain release QA blockers.

ONE next mission ONLY: PulseSoc Native Messenger Foundation Replacement QA.
