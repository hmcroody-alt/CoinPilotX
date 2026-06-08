# PulseSoc Android Real Device QA Report

## Current Status

Android real-device QA is pending Play Internal Testing upload/install or manual AAB install.

Latest scroll-performance build:

- Build ID: `e5bc979b-e09a-4b6c-baac-8c994b395bec`
- Version code: `11`
- AAB: `https://expo.dev/artifacts/eas/unoS8dwQwNwgydMAFFLmDb.aab`

## Build Requirements

- Package: `com.pulsesoc.app`
- Version code: `11`, higher than previous versionCode `10`
- Distribution: Google Play Internal Testing or Internal App Sharing

## Checklist

- Install latest AAB through Play Internal Testing.
- Log in.
- Verify auth state across Feed, Reels, Videos, Chats, Alerts, Profile, and Premium.
- Verify no placeholder icons.
- Verify website-backed UI renders exactly from `https://pulsesoc.com`.
- Verify smooth scrolling.
- Verify push permission and token registration.
- Send a test notification.
- Confirm banner, sound, vibration, and deep link.

## Current Blocker

Play submission needs the Google Play service account JSON or manual Play Console upload.
