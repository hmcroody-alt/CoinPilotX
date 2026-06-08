# PulseSoc Android Real Device QA Report

## Current Status

Android real-device QA is pending a fresh build that includes the scroll-performance changes.

## Build Requirements

- Package: `com.pulsesoc.app`
- Version code: higher than previous Play upload
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
