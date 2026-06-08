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
- iOS export: pending after final validation.
- Android export: pending after final validation.
- Real-device scroll QA: pending new TestFlight/Internal Testing install.

## Remaining Performance Work

- Optimize `/api/pulse/communications/conversations` query fan-out.
- Add production request tracing review after this build is live.
- Capture real-device FPS/video evidence from iPhone and Android.
