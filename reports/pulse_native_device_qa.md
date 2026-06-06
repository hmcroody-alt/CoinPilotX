# Pulse Native Device QA

Date: 2026-06-06

## Summary

The Pulse React Native foundation is ready for physical-device QA. Local static checks, Expo configuration checks, native audits, and a web export smoke test passed.

Physical iPhone and Android interaction still needs to be completed on real devices. This report separates what was verified locally from what requires a phone.

## Local QA Completed

- Native TypeScript typecheck: pass.
- Expo Doctor: pass, 17/17 checks.
- Native foundation audit: pass.
- Native authentication audit: pass.
- Native feed/API audit: pass.
- Python native foundation audits: pass.
- Expo config resolves:
  - App name: Pulse
  - Scheme: `pulse`
  - iOS bundle ID: `com.pulsesoc.app`
  - Android package: `com.pulsesoc.app`
  - API base URL: `https://pulsesoc.com`
- Expo web export smoke test: pass.

## Web Preview Dependency Fix

Expo web export initially failed because the native project did not include the Expo web runtime packages. Added the Expo SDK 51-compatible packages:

- `react-native-web`
- `react-dom`
- `@expo/metro-runtime`

After installation, `npx expo export --platform web --output-dir dist-web-qa` succeeded. The generated `dist-web-qa` output was removed and not committed.

## Dependency Audit

`npm audit --audit-level=high` reports transitive vulnerabilities in the Expo/React Native dependency tree.

Important detail: the suggested fixes require breaking upgrades, including Expo 56 and React Native 0.85+. Those upgrades were not applied during this QA pass because this app is pinned to Expo SDK 51 and the immediate goal is foundation validation.

Follow-up recommendation:

- Plan a controlled Expo SDK upgrade after physical-device QA confirms the baseline app behavior.
- Do not run `npm audit fix --force` casually; it changes the native SDK line.

## Physical Device QA Pending

Run on a real iPhone:

- Open the app through Expo Go or a development build.
- Verify login.
- Verify registration.
- Verify password recovery.
- Verify logout.
- Verify session restore after closing and reopening the app.
- Verify Feed loads.
- Verify Reels load.
- Verify Videos load.
- Verify Messages load.
- Verify Notifications load.
- Verify Profile loads.
- Verify Premium loads.
- Allow notification permission.
- Confirm push token registration.
- Send a test notification.
- Tap the notification and confirm routing into Pulse.

Run on a real Android device:

- Repeat the same flow.
- Confirm notification channel behavior.
- Confirm back-button behavior.
- Confirm app resumes correctly from background.

## Media QA Pending

- Reels playback.
- Video playback.
- Muting/unmuting.
- Scroll pause/play.
- Upload path readiness.
- Network loss and retry behavior.

## Launch Readiness Result

Status: native foundation ready for device QA.

Not ready yet:

- TestFlight build.
- Google Play internal test build.
- Production App Store submission.
- Production Play Store submission.

Required before store builds:

- Apple Developer account access.
- Google Play Console access.
- Firebase project.
- APNs/FCM push credentials.
- Expo/EAS project ID.
- Physical-device QA pass.
