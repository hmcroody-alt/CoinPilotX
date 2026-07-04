# PulseSoc Native Live Discovery + Live Viewer Progress

Date: 2026-07-04

## Scope

This foundation adds native Live discovery and viewer support only.

It does not build native Go Live, Studio, hosting, co-hosting, camera publishing, microphone publishing, restream controls, or native LiveKit host controls. Those flows remain on the existing safe web fallback until a separate native LiveKit hosting phase is planned.

## Existing PulseSoc Reuse

Reused existing backend/API/database/business logic:

- `GET /api/pulse/live-now`
- `GET /api/pulse/live/<live_id>/state`
- `POST /api/pulse/live/<live_id>/join`
- `GET/POST /api/pulse/live/<live_id>/chat`
- `POST /api/pulse/live/<live_id>/react`
- existing LiveKit/Mux playback manifest logic
- existing LiveKit direct fallback state
- existing live room/session database
- existing `pulse_live_sessions`, `pulse_live_chat`, `pulse_live_viewers`, and `pulse_live_reactions` tables
- existing live viewer count/state logic
- existing live chat moderation behavior
- existing live reaction behavior
- existing Live web viewer and Studio fallback routes

The native app remains a client of the existing PulseSoc Live platform. It does not duplicate LiveKit token generation, Mux egress rules, creator eligibility, co-host approval, moderation, replay creation, destination routing, or feed insertion logic.

## Native Implementation

Added:

- `mobile-native/src/api/live.ts`
- `mobile-native/src/screens/LiveScreen.tsx`

Navigation added:

- Native `Live` tab.
- Native `LiveDetail` stack route.
- Native linking for `/pulse/live` and `/pulse/live/<live_id>`.
- Notification/deep-link routing for `/pulse/live`, `/pulse/live/<id>`, and `/pulse/reels?live=<id>`.
- Safe web fallback for `/pulse/live/studio`.

Native Live discovery includes:

- Live now list through the existing `/api/pulse/live-now` API.
- Scheduled/events section placeholder that only fills when an existing API returns scheduled payloads.
- Pull to refresh.
- Offline metadata cache.
- Empty, loading, error, and offline states.
- Host/profile navigation hook where host identity is present.

Native Live viewer includes:

- Live detail/viewer shell.
- Native video shell using existing playback URLs from the server playback manifest.
- Web fallback for unsupported playback transports.
- Join viewer state through existing `POST /api/pulse/live/<id>/join`.
- Local leave/close state. A dedicated backend viewer-leave endpoint was not found in the current production codebase.
- Viewer count/state refresh from `GET /api/pulse/live/<id>/state`.
- Live chat read/send through existing chat API.
- Live reactions through existing reaction API.
- Share hook using the existing PulseSoc Live/Reels URL.

## Web Fallbacks Preserved

Kept on web fallback:

- Go Live.
- Studio.
- Host controls.
- Co-hosting.
- Restream destinations.
- Unsupported playback transports.
- Native camera/mic publishing.

This keeps the current production WebView app and browser Live Studio untouched and usable.

## Device-Only Behavior Not Verified

The following were not marked as passed without simulator or real-device tooling:

- HLS playback smoothness and audio behavior on iOS/Android.
- Live background/foreground recovery.
- Viewer count refresh while app is backgrounded.
- Live chat keyboard ergonomics on small devices.
- Mux HLS versus LiveKit direct fallback behavior on real devices.
- Long-running Live memory and battery behavior.

## Next Recommendation

Recommended next native feature: Native Live Viewer Device QA + Hardening.

Why this comes next:

- Live playback is more device-sensitive than previous static/list surfaces.
- Native hosting should not start until viewer playback, chat, reactions, deep links, refresh, and fallback behavior are verified.
- The current native Live foundation intentionally keeps hosting on web fallback, so the safest next step is QA and hardening rather than expanding scope.

Reusable existing PulseSoc logic:

- Existing Live APIs and playback manifest.
- Existing LiveKit/Mux backend state and direct fallback.
- Existing live chat/reaction/viewer tables.
- Existing native Live API wrapper and viewer screen.
- Existing notification/deep-link routing.

What must be rebuilt/verified natively:

- Real-device video playback behavior.
- HLS buffering/retry behavior.
- Keyboard/chat ergonomics.
- App foreground/background recovery.
- Unsupported playback web fallback behavior.
- Deep link handling from notifications into the viewer.

Risk: Medium-high.

Complexity: Medium.

Safest implementation plan:

1. Verify Live discovery on simulator or real device.
2. Verify supported playback URLs render in native video.
3. Verify unsupported LiveKit direct/co-host/Studio paths open web fallback.
4. Verify chat send/read and reactions with an authenticated account.
5. Verify foreground/background state refresh.
6. Fix only blockers found during device QA.
7. Keep native hosting out of scope until viewer behavior is stable.
