# Pulse Mobile Firebase iOS/Android Setup Report

Date: 2026-06-07

## Firebase Project

- Project name: `PulseSoc`
- Project ID visible in console: `pulsesoc-3e0a6`

## Mobile Identifiers

- App display name: `Pulse`
- Android package: `com.pulsesoc.app`
- iOS bundle identifier: `com.pulsesoc.app`
- Deep link scheme: `pulse`
- Production API base URL: `https://pulsesoc.com`

## Firebase App Registration

- Android app: registered in Firebase as `Pulse Android`
- Android package: `com.pulsesoc.app`
- SHA-1: skipped, not required for this phase
- iOS app: registered in Firebase as `Pulse iOS`
- iOS bundle ID: `com.pulsesoc.app`
- App Store ID: left blank

## Config File Status

Expo config now references:

- Android: `./credentials/firebase/google-services.json`
- iOS: `./credentials/firebase/GoogleService-Info.plist`

The QA browser confirmed both config-file download buttons, but Codex in-app browser downloads are not supported. No Firebase config file contents were printed or exposed.

Manual download steps:

1. Open Firebase Console.
2. Open project `PulseSoc`.
3. Go to Project Overview, then the app list or Project Settings.
4. For `Pulse Android`, download `google-services.json`.
5. Place it at `mobile/pulse-react-native/credentials/firebase/google-services.json`.
6. For `Pulse iOS`, download `GoogleService-Info.plist`.
7. Place it at `mobile/pulse-react-native/credentials/firebase/GoogleService-Info.plist`.
8. Review both files before committing. Do not place service account JSON, APNs private keys, Google Play service account files, or other private keys in this folder.

## Notification Readiness

- Permission request: present through `expo-notifications`.
- Device token capture: present through `Notifications.getExpoPushTokenAsync`.
- Platform handling: Android notification channel is configured.
- Token registration: posts native Expo token to `/api/push/subscribe`.
- Token cleanup: logout now calls `/api/push/unsubscribe`.
- Notification tap routing: uses notification response listener and opens deep links.
- Real push sends: not enabled in this task.

## Backend/Railway Variables Needed Later

Do not create or expose these until push sending is explicitly approved:

- `FCM_PROJECT_ID`
- `FCM_CLIENT_EMAIL`
- `FCM_PRIVATE_KEY`
- `APNS_KEY_ID`
- `APNS_TEAM_ID`
- `APNS_BUNDLE_ID`
- `APNS_PRIVATE_KEY`
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`

## QA Results

- App Store Connect login verified; `PulseSoc` app record is visible in Prepare for Submission.
- Firebase project access verified.
- Firebase Android app registration completed.
- Firebase iOS app registration completed.
- Expo config resolves Android package and iOS bundle identifiers correctly.
- Firebase config references are present in Expo config.
- Deep links still use `pulse://`.
- No Firebase secrets, private keys, or config file contents were printed.

## Remaining Manual Steps

- Manually download and place `google-services.json`.
- Manually download and place `GoogleService-Info.plist`.
- Log into Expo/EAS locally before creating or uploading builds.
- Configure APNs/FCM sending credentials only when native push sending is approved.
- Run real-device iOS and Android notification QA after builds include the Firebase config files.
