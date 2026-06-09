# Android WebView Mirror Fix Report

Updated: 2026-06-09

## Problem

Android was still able to show a native React Native welcome/onboarding layer before the website. That made Android feel different from the iOS WebView build and from the live PulseSoc website.

## Decision

The PulseSoc mobile app must mirror the live website. Android and iOS should both launch the same WebView shell and load:

`https://pulsesoc.com`

The website is the source of truth for:

- Welcome/logged-out experience
- Signup
- Sign-in
- Language behavior
- Feed
- Reels
- Videos
- Messages
- Notifications
- Profile
- Premium

## Fix Applied

- Removed the native welcome launch branch from `App.tsx`.
- Removed the hidden native session-check startup gate.
- Removed native welcome CTA routing from the shell.
- Kept WebView shell capabilities:
  - shared cookies
  - DOM storage
  - inline media playback
  - pull-to-refresh
  - Android hardware back navigation
  - external-link handoff
  - deep-link conversion
  - native push bridge
  - native share bridge
  - offline fallback

## Expected Android Behavior

- App opens the live PulseSoc website immediately.
- Logged-out users see the website's logged-out/welcome/login flow.
- Logged-in users remain in the website session and can use the product.
- No native placeholder screens, native welcome screen, native debug cards, or native bottom-tab rebuilds are shown.

## Validation

- `npm run typecheck`: PASS
- `npm run audit:mobile-web-parity`: PASS
- `npm run audit:android-ui`: PASS
- `npm run audit:feed`: PASS
- `npm run audit:mobile-performance`: PASS

## Store Impact

The previous Android build should not be uploaded. Build a new Android AAB after this fix and upload only that new version to Google Play Internal Testing.
