# Pulse React Native Foundation

Date: 2026-06-06

## Summary

Pulse now has a controlled React Native foundation at `mobile/pulse-react-native`. The scaffold is intentionally narrow: auth, session persistence, core navigation, API reuse, notification token registration, and deep-link routing.

## Foundation Coverage

- Authentication: login, registration, password recovery, logout, session restore.
- Navigation: Feed, Reels, Videos, Messages, Notifications, Profile.
- API strategy: reuse existing Pulse APIs and add only native-safe auth/session helpers.
- Notification strategy: Expo token collection posts into the existing Pulse push subscription pipeline.
- Deep links: `pulse://`, `https://pulsesoc.com`, and `https://coinpilotx.app`.
- Environment strategy: `EXPO_PUBLIC_PULSE_API_BASE_URL` controls target API host.

## Current App Boundary

The scaffold renders API-backed list screens instead of rebuilding business logic. This keeps the native project aligned with the production web backend while Pulse stabilizes.

## Risks To Verify

- Native cookie handling must be tested on iOS and Android.
- Expo push token delivery needs real-device testing.
- Media-heavy surfaces need native video player work after the web player remains stable.
- Auth UX should receive full QA before any store submission.

## Status

Ready for foundation review and device QA. Not ready for App Store or Play Store build generation.
