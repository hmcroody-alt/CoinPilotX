# PulseSoc Notification Sound and Vibration Fix Report

Date: 2026-06-08

## Issue

Real-device QA showed PulseSoc notifications appearing without ringing or vibrating.

## Root Cause

- The backend Expo push payload already requested `sound: default`, high priority, and the `default` Android channel.
- The native app only created the Android notification channel during push token registration, so a device could receive a push before the channel was refreshed with sound/vibration settings.
- PulseSoc website notification tests and live in-app alerts relied on browser audio and `navigator.vibrate`, which are unreliable inside a native WebView.

## Fixes Applied

- Android notification channel is now configured on app startup with:
  - channel id `default`
  - max importance
  - default sound
  - vibration enabled
  - explicit vibration pattern
- Foreground native push notifications now trigger a device vibration listener.
- Added a native WebView bridge method: `PulseSocNative.notify()`.
- Website live notification alerts and notification sound/vibration tests now call the native bridge when the app is running inside the native shell.
- Native bridge schedules a local sounding notification for foreground website alerts so the device has a real native ring/vibrate path.
- Native notification tap routing remains pointed at the PulseSoc WebView route.

## Validation

- Mobile TypeScript: passed.
- Mobile notification audit: passed.
- Full mobile audit suite: passed.
- Expo Doctor: passed.
- Expo public config resolves PulseSoc app identifiers and notification plugin config.
- Python compile for push/notification backend files: passed.
- JavaScript parse for website notification script: passed.
- Pulse native push delivery audit: passed.
- Pulse notification web push audit: passed.
- Site functional audit: passed with expected protected-route warnings only.
- Performance audit: passed with zero warnings/failures.

## Real Device QA Needed

New builds were created from commit `d71dc3010d6f07286e03eb6ffe6cf1ae72247777`.

- iOS build number: `16`
- iOS EAS build: `bb555c70-3afd-41f1-9afd-bde5c398d794`
- iOS artifact: `https://expo.dev/artifacts/eas/p8GvBvMsHkYPLmF74DWME5.ipa`
- iOS App Store Connect submission: uploaded successfully and processing for TestFlight.
- Android versionCode: `12`
- Android EAS build: `00d19c22-e843-45e1-a94e-dcc7395150ef`
- Android artifact: `https://expo.dev/artifacts/eas/6S8hQLw2C9anMwYRZ2QAB.aab`

Install the new builds, then test:

- Notification Settings > test push.
- Notification Settings > test sound.
- Notification Settings > test vibration.
- Direct message notification.
- Mention/reply notification.
- Background app notification.
- Foreground app notification.

Expected result:

- iPhone receives visible notification and vibrates/rings when device settings allow it.
- Android receives visible notification through the `default` channel and vibrates/rings when device settings allow it.

## Notes

Device-level silent mode, Focus/Do Not Disturb, Android per-channel notification settings, or muted app notification settings can still suppress sound or vibration. If the new build remains silent, verify PulseSoc notification channel settings on Android and Focus/Silent Mode on iPhone before changing backend credentials.
