# Native Live Broadcasting — Phase 1 Report

Date: 2026-07-19
Branch: `release/undx-nexus-core-v4`
Targets validated: physical iPhone **P3r7or** (iPhone 16 Pro) + Xcode Simulator (PulseSoc iPhone 16 Pro). Both `Build Succeeded`, 0 errors, app installed.

## Scope of this phase

Acceptance criterion #1 of the mission — **"Pressing Go Live must never open the website"** — plus a real native LiveKit host broadcast and guest-request handling. This is the non-negotiable core; the remaining phases (viewer-in-Reels, mixed-media renderers, multi-guest layout engine, invite sheet, moderation, replay viewer) are not part of this phase and are listed under "Not yet built".

## What is real and shipped

### 1. Web Live Studio redirect fully removed
Swept the entire native app for the forbidden `/pulse/live/studio` web handoff. Every "Go Live" / Live Studio entry point now navigates to the native `LiveStudio` screen or the native host session — **no `Linking.openURL`, Safari, or WebView remains on any going-live path**:

- `LiveStudioScreen.goLive()` — now calls `startLive(draft)` then navigates to the native `NativeLiveHost` route (was: web handoff URL).
- `CameraStudioScreen.openLiveStudio()` — navigates to native `LiveStudio` (was: `Linking.openURL(.../pulse/live/studio?context_type=native_camera)`); dead `liveStudioUrl` removed.
- `EventsScreen` — both "Studio Web" / "Live Studio" gateways navigate to native `LiveStudio` (was: `openEventsWebFallback("studio")`). The `"studio"` mode was deleted from `openEventsWebFallback` entirely.
- `notificationRouting.ts` — `/pulse/live/studio` deep links navigate native instead of `Linking.openURL`.
- `CreatorStudioScreen.tsx` — "Live Studio Web" action relabeled and rewired native.
- `liveStudioReadiness.ts` — removed the dead `liveStudioHandoffUrl()` helper that built the forbidden URL.
- `masterNavigation.ts` — stale "hands off to the production web studio" description corrected.

`openLiveWebFallback()` / `liveWebUrl()` survive only as a **viewer** web link (Reels URL), never a host/going-live path.

### 2. Real native LiveKit host broadcast
- `src/live/liveSession.ts` — pure request/response contract layer (payload builder + normalizers), fully unit-tested.
- `src/api/live.ts` — `startLive`, `getLiveKitToken(role)`, `endLive`, `listJoinRequests`, `respondToJoinRequest`, `requestToJoinLive`, `cancelJoinRequest` against the real Python `bot.py` routes (verified: `POST /api/pulse/live/start`, `POST /api/pulse/live/<id>/livekit/token`, `POST /api/pulse/live/<id>/end`, `GET/POST .../join-requests...`).
- `src/live/useLiveBroadcastRoom.ts` — LiveKit room hook exposing a participant **array** (host + remote guests), mic/camera/speaker/flip controls, active-speaker + mute tracking. Reuses the WebRTC native module already compiled into the binary — **no native rebuild required**.
- `src/screens/LiveHostSessionScreen.tsx` — mints a **host** token, verifies `canPublish`, connects with `publish: true`, renders live `VideoView` tiles, LIVE badge + elapsed timer + viewer count (5s poll), accept/deny guest rows, and End Live with confirm → `endLive` → disconnect.

### 3. Anti-fakery honesty (verified in code)
- If the backend returns no publish token or `canPublish === false`, the host screen shows a **fatal error** ("PulseSoc did not grant a publish token for this broadcast. It cannot go live.") — it does **not** render a fake camera preview or claim to be broadcasting.
- `startLive` throws the backend's real message when no usable live id comes back.
- `getLiveKitToken` returns `null` (surfaced as an error) rather than fabricating credentials.

## Verification
- `npx tsc --noEmit` — clean (0 errors).
- `npx jest` — 15 suites / 175 tests passing (includes 16 new `liveSession` tests).
- Physical P3r7or: `Build Succeeded`, installed 100%.
- Simulator: `Build Succeeded`, installed and launched.

Note: full end-to-end broadcast (camera actually publishing to viewers) requires a physical device with camera + a backend that grants a host publish token; the simulator validates build/nav but not live capture.

## Not yet built (later phases)
Viewer mode inside Reels, unified mixed-media infinite feed renderers (video/photo/carousel/livestream/replay), multi-guest layout engine, guest invite sheet, in-broadcast moderation UI, replay viewer.

## Documented backend blockers (DB columns exist, NO API yet)
These cannot be honestly wired until contracts exist. Required endpoints:

| Capability | DB evidence | Required contract (proposed) |
|---|---|---|
| Mute a guest | `pulse_live_guests.audio_muted` | `POST /api/pulse/live/<id>/guests/<userId>/mute {muted: bool}` |
| Remove/kick a guest | (guest row) | `POST /api/pulse/live/<id>/guests/<userId>/remove` |
| Delete a chat message | chat table | `POST /api/pulse/live/<id>/chat/<messageId>/delete` |
| Timeout/suspend a viewer | — | `POST /api/pulse/live/<id>/moderation/timeout {userId, seconds}` |
| Replay finalization | LiveKit egress | `endLive` currently returns `recording_status`/`replay_url`; egress→replay asset finalization is not confirmed wired server-side |

Until these ship, the native UI must not present these actions as working.
