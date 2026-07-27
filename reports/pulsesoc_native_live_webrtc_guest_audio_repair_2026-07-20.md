# PulseSoc Native Live WebRTC Guest Playback and Host Audio Repair — 2026-07-20

## Scope

Focused P0 repair for native Live guest playback and host microphone publication. This keeps the existing production PulseSoc Live backend, token route, chat, reaction, join, discovery, LiveKit, and media coordinator contracts. It does not create a second Live provider or alter the production WebView path.

## Production Sources Inspected

- `static/js/pulse_live_studio.js`
  - WebRTC viewer/publisher flow uses production `/api/pulse/live/<id>/webrtc/signal` and `/api/pulse/live/<id>/webrtc/signals`.
  - LiveKit-backed path uses `/api/pulse/live/<id>/livekit/token`.
  - Viewer code subscribes to remote audio/video and only falls back when no playable transport is available.
- `static/js/pulse_live_studio_runtime.js`
  - LiveKit token route, publish-complete, guest management, chat, reaction, and cohost flows.
- `services/live_distribution_service.py`
  - Server exposes `supports_webrtc`, `webrtc_room_id`, `supports_hls`, `playback_url`, and `preferred_transport`.
- `mobile-native/src/components/reels/ReelLiveViewerSurface.tsx`
  - Existing native LiveKit viewer strategy already joins as `viewer`, renders real subscribed tracks, and falls back to HLS only when needed.

## Root Causes

- Native Live Detail treated only HLS URLs as native-playable. WebRTC-only lives with `webrtc_room_id` or `livekit.room` were classified as unsupported and showed the generic `Playback fallback required` Web fallback message.
- The shared LiveKit hook enabled the microphone and camera but did not verify that a local microphone audio track was actually published. A host could appear connected while audience audio was still absent.
- The Live Detail `Sound on` control only toggled the Expo HLS muted state. It did not reflect or control LiveKit remote audio tracks.

## Changes Made

- Added `liveSupportsNativeWebRtc()` in `mobile-native/src/api/live.ts`.
- Updated `mobile-native/src/screens/LiveScreen.tsx` so Live Detail:
  - joins the existing production LiveKit room with `getLiveKitToken(liveId, "viewer")`;
  - renders remote subscribed LiveKit video through the native `VideoView`;
  - shows `Connecting native Live`, `Reconnecting to Live`, or `Waiting for host media` for truthful native states;
  - only shows Web fallback when neither HLS nor LiveKit/WebRTC is available;
  - exposes LiveKit remote audio/video counts;
  - disconnects the viewer room on Leave, Close, and unmount.
- Extended `mobile-native/src/live/useLiveBroadcastRoom.ts` so the shared LiveKit hook:
  - tracks local audio publications;
  - tracks remote audio/video publication counts;
  - exposes remote audio enable/disable;
  - fails host publish with `LIVE_LOCAL_AUDIO_NOT_PUBLISHED` if microphone publication is missing;
  - logs sanitized connection diagnostics without tokens.
- Added focused regression tests in `mobile-native/src/api/__tests__/live.test.ts`.
- Added `scripts/pulsesoc_native_live_webrtc_guest_audio_repair_audit.py`.

## Backend Contracts Reused

- `/api/pulse/live-now`
- `/api/pulse/live/<id>/state`
- `/api/pulse/live/<id>/join`
- `/api/pulse/live/<id>/chat`
- `/api/pulse/live/<id>/react`
- `/api/pulse/live/<id>/livekit/token`

## Verification

- Simulator build attempted: visual simulator QA and screenshots were not completed in this pass because Xcode failed from local disk exhaustion before producing an installable simulator app.
- Code-path verified: WebRTC-only LiveKit playback no longer routes to the generic unsupported fallback.
- Code-path verified: Host publish now requires a verified local audio track publication.
- Code-path verified: Viewer sound control now targets remote LiveKit audio tracks for WebRTC rooms.
- Xcode simulator build attempted against `PulseSoc iPhone 16 Pro` (`C980AEE0-2D07-4D98-8A37-D0447A6A908B`) but failed with `errno=28` while writing `ExpoModulesCore/libExpoModulesCore.a`. This is a local disk-space failure. Generated build output was removed after the failed attempt.
- Physical-device-only: host microphone transmission to a second guest device, speaker/Bluetooth routing, background audio, and real camera/microphone edge cases.

## Physical-Device Notes

Full success for the original user-visible defect requires a real two-client check:

1. Host starts a native Live on physical iPhone.
2. Second client joins as a viewer.
3. Viewer hears host microphone audio.
4. Host mute/unmute changes actual transmitted audio.
5. Viewer Sound on/off changes playback without ending the room.

That two-client audio proof was not directly observed in this run, so it remains `Physical-device-only`.

## Status

- Native WebRTC fallback defect: fixed by code path.
- Host silent microphone publication risk: guarded by publication verification.
- HLS fallback behavior: preserved.
- Production WebView compatibility: preserved.
- Remaining release blocker: direct physical two-client audio validation.
