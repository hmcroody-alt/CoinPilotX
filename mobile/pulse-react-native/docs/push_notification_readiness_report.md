# Pulse Mobile Push Notification Readiness Report

Date: 2026-06-07

## Current Status

Pulse Mobile is ready for the next native build QA pass for push registration and notification routing. Real push sending is not enabled yet.

## Verified

- Firebase project: `PulseSoc`
- Firebase project ID: `pulsesoc-3e0a6`
- Android app registered: `Pulse Android`
- iOS app registered: `Pulse iOS`
- Android package: `com.pulsesoc.app`
- iOS bundle identifier: `com.pulsesoc.app`
- App display name: `Pulse`
- Deep link scheme: `pulse`
- Android config file path: `credentials/firebase/google-services.json`
- iOS config file path: `credentials/firebase/GoogleService-Info.plist`

## App Foundation

- Push permission flow uses `expo-notifications`.
- Android notification channel is configured.
- Expo push token is captured on physical devices.
- Device token registration posts to `/api/push/subscribe`.
- Logout cleanup posts to `/api/push/unsubscribe`.
- Notification taps route through deep links.
- Deep links support `pulse://`, `https://pulsesoc.com`, and existing compatibility prefixes.

## Safety

- Firebase mobile app config files were not printed.
- Firebase mobile app config files do not contain Firebase service-account private key markers.
- Service-account JSON, APNs private keys, Google Play service account credentials, and other private keys remain out of scope.
- Actual Firebase mobile config files are protected by the credentials ignore rules unless intentionally staged later.

## Backend Credentials Needed Later

- `FCM_PROJECT_ID`
- `FCM_CLIENT_EMAIL`
- `FCM_PRIVATE_KEY`
- `APNS_KEY_ID`
- `APNS_TEAM_ID`
- `APNS_BUNDLE_ID`
- `APNS_PRIVATE_KEY`
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`

## Remaining QA

- Log into Expo/EAS locally.
- Create or link the EAS project ID.
- Build iOS and Android internal builds.
- Install on real devices.
- Confirm permission prompts.
- Confirm token registration reaches the backend.
- Send controlled test notifications only after APNs/FCM send credentials are explicitly approved.
- Confirm notification tap routing for messages, groups, rooms, communities, channels, posts, reels, videos, and notifications.
