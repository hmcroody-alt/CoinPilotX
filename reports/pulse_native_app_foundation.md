# Pulse Native App Foundation

Date: 2026-06-06

Status: React Native foundation started.

## What Changed

- Created `mobile/pulse-react-native` as an Expo React Native foundation.
- Added navigation for Feed, Reels, Videos, Messages, Notifications, and Profile.
- Added a shared API client pointed at `https://pulsesoc.com` by default.
- Added secure session persistence through Expo SecureStore.
- Added login, registration, password recovery, and logout flow scaffolding.
- Added push token registration through the existing Pulse push subscription endpoint.
- Added deep-link prefixes for `pulse://`, `https://pulsesoc.com`, and `https://coinpilotx.app`.

## Backend Support Added

- `/api/mobile/auth/session`
- `/api/mobile/auth/login`
- `/api/mobile/auth/register`
- `/api/mobile/auth/recover`
- `/api/mobile/auth/logout`
- `/api/pulse/profile/me`

These routes are additive and do not change website login, auth callbacks, secrets, Railway configuration, Brevo, Mux, Stripe, or webhook behavior.

## Boundary

This is not an App Store or Play Store production build. No native `ios/` or `android/` project has been generated yet.

## Remaining Before Store Work

- Confirm native cookie/session behavior on real iOS and Android devices.
- Add production media players for reels/videos after web media stabilization remains clean.
- Configure Firebase/APNs only after notification delivery is verified end to end.
- Add crash reporting and native analytics after privacy disclosure review.
