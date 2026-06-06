# Pulse React Native Foundation

Date: 2026-06-06

## Summary

Pulse now has a controlled React Native foundation at `mobile/pulse-react-native`. The app is an Expo + React Native + TypeScript project with a buildable `src/` entrypoint, native auth/session handling, core navigation, API reuse, notification token registration, and deep-link routing.

## Foundation Coverage

- Authentication: login, registration, password recovery, logout, session restore.
- Navigation: Feed, Reels, Videos, Messages, Notifications, Profile, Premium.
- API strategy: reuse existing Pulse APIs and add only native-safe auth/session helpers.
- Notification strategy: Expo token collection posts into the existing Pulse push subscription pipeline.
- Deep links: `pulse://`, `https://pulsesoc.com`, `https://www.pulsesoc.com`, `https://coinpilotx.app`, and `https://www.coinpilotx.app`.
- Environment strategy: `EXPO_PUBLIC_PULSE_API_BASE_URL` controls target API host.
- Bundle/package IDs: `com.pulsesoc.app`.

## Implemented This Pass

- Normalized the native workspace into `mobile/pulse-react-native`.
- Added `src/App.tsx` as the active native app entrypoint.
- Added a SecureStore-backed API client in `src/api/client.ts`.
- Added `AuthProvider` for session restore, login, registration, password recovery, logout, and refresh.
- Added `AuthScreen` with login, register, and recovery modes.
- Added reusable API-backed list screens for Pulse surfaces.
- Added native tab navigation for Feed, Reels, Videos, Messages, Notifications, Profile, and Premium.
- Added Expo notification token registration through `/api/push/subscribe`.
- Added notification click routing through Pulse deep links.
- Updated Expo bundle identifiers to `com.pulsesoc.app`.

## Current App Boundary

The scaffold renders API-backed list screens instead of rebuilding business logic. This keeps the native project aligned with the production web backend while Pulse stabilizes. The older `mobile/` scaffold remains as source material; the active app for this milestone is `mobile/pulse-react-native`.

## Risks To Verify

- Native cookie handling must be tested on iOS and Android.
- Expo push token delivery needs real-device testing.
- Media-heavy surfaces need native video player work after the web player remains stable.
- Auth UX should receive full QA before any store submission.
- Stripe Premium must be reviewed against Apple/Google in-app purchase rules before store release.

## Status

Ready for foundation review and device QA. Not ready for App Store or Play Store production build generation.
