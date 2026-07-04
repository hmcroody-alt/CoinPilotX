# PulseSoc Native App Migration Plan

Date: 2026-07-04

## Strategy

Develop PulseSoc Native as a separate Expo React Native app under `mobile-native/`. The current live WebView app stays in production until native parity is proven through staged QA.

## Phase 1: Foundation

Status: started in `mobile-native/`.

Deliverables:

- Native app shell with Expo and React Navigation.
- Login, signup, session restore, and logout using existing mobile auth APIs.
- Push notification permission and Expo token registration.
- Mission Control screen connected to `/api/dashboard/mission-control`.
- Messenger list connected to `/api/pulse/messages/conversations`.
- Basic chat detail and send flow.
- Pulse AI chat connected to `/api/pulse/assistant/chat`.
- Profile summary connected to `/api/pulse/profile/me`.
- Settings surface for push and session controls.

Exit criteria:

- Runs locally with `npm install` and `npm run start`.
- TypeScript passes.
- Real-device login, message load, message send, and push registration pass.
- No current WebView shell files are required for native boot.

## Phase 2: Native Media

Deliverables:

- Reels native full-screen player.
- Status viewer.
- Status creator.
- Native image/video viewer.
- Camera upload.
- Image/video compression.
- Upload progress, retry, and processing status.

Exit criteria:

- Smooth Reels/Status playback on supported iOS and Android devices.
- Sound and autoplay behavior matches current PulseSoc policy.
- Camera and media permissions degrade cleanly.
- Uploads use existing backend media contracts.

## Phase 3: Native Calls

Deliverables:

- Native LiveKit voice/video calls.
- Full-screen incoming calls.
- Native call ringing.
- Background audio handling.
- Camera flip.
- Mic, speaker, and Bluetooth controls.

Exit criteria:

- Calls connect reliably on real devices.
- Incoming call UI appears while foregrounded, backgrounded, and locked where platform policy allows.
- Audio route controls behave correctly.
- Call teardown and reconnect paths do not leave stale backend state.

## Phase 4: Native Growth And Premium Surfaces

Deliverables:

- Native notification center.
- Intelligence alerts.
- Growth Center.
- Crypto/market alerts.
- Premium status and upgrade routing.
- Creator tools.

Exit criteria:

- Native surfaces preserve server-side entitlements and moderation controls.
- Premium and billing flows preserve existing provider constraints.
- Notification categories, deep links, sounds, vibrations, and badges pass device QA.

## Release Plan

1. Keep WebView production app live.
2. Develop native app behind `mobile-native/` only.
3. Use backend-compatible test users and staging/prod-safe API checks.
4. Run native app foundation audit on every native-scope change.
5. Add feature-specific audits for media, push, calls, and premium as phases land.
6. Start TestFlight/internal testing only after Phase 1 real-device checks pass.
7. Submit to App Store only after all listed no-submit gates pass.
8. Gradually route cohorts to native after parity confidence, with rollback to the current WebView app.

## No-Submit Gates

Do not submit the native app to the App Store until:

- Login works.
- Messages work.
- Notifications work.
- Reels and Status are smooth.
- Calls are native and stable.
- No major feature regressions remain.

## Production Safety

- Do not modify the current production WebView app as part of native foundation work.
- Do not duplicate backend business logic in the client.
- Do not ship fake success for unsupported native capabilities.
- Do not migrate users until backend logs, device QA, and feature parity checks support the rollout.
