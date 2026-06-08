# PulseSoc Mobile Performance Report

## What Changed

- Converted mobile performance strategy to match the WebView shell architecture.
- Added `mobile_performance_audit.js` and wired it into `npm run audit`.
- Enabled WebView cache and hardware composition.
- Added iOS scroll tuning props.
- Reduced website-side media and polling work during scroll.

## Mobile WebView Tuning

- `cacheEnabled`
- `androidLayerType="hardware"`
- iOS `decelerationRate`
- disabled automatic content inset adjustment
- preserved native bounce/overscroll behavior
- kept cookies/session sharing enabled

## Website Work That Directly Helps iOS/Android

- Mobile media performance mode disables expensive ambient color sampling.
- Mobile media cards render only the required backdrop/vignette layers.
- Full image upgrades are skipped in mobile performance mode.
- Autoplay waits until the active visible video is selected and scroll settles.
- Notification polling is throttled and scroll-aware.

## Current QA

- Mobile performance source audit: passed.
- iOS export: passed.
- Android export: passed.
- iOS EAS build: `061174a9-0dc1-4c7c-a3a9-4c7713d2261d`, build number `15`, uploaded to App Store Connect processing.
- Android EAS build: `e5bc979b-e09a-4b6c-baac-8c994b395bec`, versionCode `11`, AAB ready.
- Real-device scroll QA: pending new TestFlight/Internal Testing install.

## Remaining Performance Work

- Optimize `/api/pulse/communications/conversations` query fan-out.
- Add production request tracing review after this build is live.
- Capture real-device FPS/video evidence from iPhone and Android.
