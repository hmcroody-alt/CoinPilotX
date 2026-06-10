# PulseSoc Native Live Camera Broadcasting

Date: 2026-06-09

## Decision

PulseSoc will use LiveKit for realtime native broadcasting and Mux for recording/playback/archive. LiveKit was selected over Agora for this sprint because the Railway environment already has `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`, and the React Native SDK supports Expo development builds through the official LiveKit Expo plugin. This is the fastest stable path while preserving the existing PulseSoc WebView app shell.

## Implemented

- Added backend LiveKit token minting at `/api/pulse/live/<live_id>/livekit/token`.
- Tokens are short lived, signed server-side, and secrets are never sent to the client except the resulting scoped token.
- Host tokens require the logged-in user to own the live session or be an admin.
- `/api/pulse/live/start` now returns LiveKit readiness metadata.
- Added native iOS/Android live overlay in the mobile app:
  - VisionCamera preview.
  - Camera and microphone permission request.
  - Front/back camera preview switch before start.
  - Microphone mute toggle.
  - Start Live.
  - LiveKit room connection and camera/microphone publish.
  - End Live.
  - Viewer count polling.
  - Live chat send/display.
  - Live reactions.
- Added authenticated WebView bridge so native live calls use the already logged-in website session.
- Existing backend already creates LIVE feed posts and updates them when a live ends.
- Existing backend already indexes Mux replay into Videos when a recording playback URL exists.

## Website/iOS Behavior

- The mobile app remains a WebView shell, so Feed, Reels, Videos, Messages, Notifications, Profile, and Premium continue to mirror the live website.
- Existing web Go Live buttons now open the native live overlay inside the app through `window.PulseSocNative.goLive()`.
- Browser/Desktop web keeps the existing browser studio flow.

## Reaction/View Repairs

- Post, status, reel, and video reaction buttons now light up with a stronger glow/pressed state.
- Status and video reaction handlers set the active state immediately and roll back on failure.
- Reels show view counts using `replay_count`.
- Reels send a view event when playback starts.
- Posts now display view counts in feed metadata.

## Remaining Required Real QA

This work is not launch-complete until a real two-account device test verifies:

- Creator starts Live from iPhone.
- Second account joins from another device.
- Viewer sees live video.
- Live chat appears both ways.
- Reactions appear.
- Viewer count updates.
- Live notification appears.
- LIVE feed post opens the live room.
- End Live works.
- Mux recording completes.
- Replay appears in Videos.

## iOS Build and Submission

- Commit: `b3d39eaf Add PulseSoc native live camera broadcasting`
- EAS iOS build ID: `1344654f-0816-4dff-ac35-2fc5c08098dc`
- App version: `0.1.1`
- iOS build number: `25`
- IPA: `https://expo.dev/artifacts/eas/3nZAZeXswQN5AgcJKvRsAG.ipa`
- EAS submission ID: `6455fc6d-a874-43cf-8980-a39648882119`
- App Store Connect status at submit time: binary uploaded successfully and processing by Apple.
- Android: intentionally deferred until tomorrow per launch direction.

## Validation

- Python compile: passed for `bot.py`, `services/live_feed_service.py`, and `services/live_stream_engine.py`.
- Git diff whitespace check: passed.
- Mobile typecheck: passed.
- Mobile audit suite: passed, including native live, Firebase, notifications, performance, web parity, and store readiness audits.
- Expo config: passed and resolves package `com.pulsesoc.app`, bundle `com.pulsesoc.app`, SDK 55, and production Xcode 26.2 image.
- Expo Doctor: 17/19 checks passed. Remaining warnings are:
  - `@expo/config-plugins` is installed directly because the LiveKit Expo plugin imports it directly during config resolution.
  - React Native Directory has no metadata for `@livekit/react-native-expo-plugin`, and `react-native-nitro-image` is untested on New Architecture.
  These warnings did not block the successful iOS production build.

## Security Notes

- No LiveKit, Mux, APNs, FCM, or Stripe secrets were committed.
- Mobile code only requests scoped LiveKit tokens through the authenticated web session.
- LiveKit token endpoint returns `403` if a non-host asks for publish access.

## Files Changed

- `bot.py`
- `static/css/pulse_desktop_feed.css`
- `static/css/pulse_reels_experience.css`
- `static/css/pulse_status_system.css`
- `mobile/pulse-react-native/App.tsx`
- `mobile/pulse-react-native/index.ts`
- `mobile/pulse-react-native/app.json`
- `mobile/pulse-react-native/package.json`
- `mobile/pulse-react-native/package-lock.json`
- `mobile/pulse-react-native/components/NativeLiveBroadcast.tsx`
- `mobile/pulse-react-native/scripts/native-live-audit.js`
