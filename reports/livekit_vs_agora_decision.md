# LiveKit vs Agora Decision

Date: 2026-06-09

## Recommendation

Use LiveKit for PulseSoc native live broadcasting.

## Why LiveKit Wins For This Sprint

- Railway already has `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
- PulseSoc can mint scoped host/viewer tokens from the existing authenticated backend.
- The React Native SDK supports Expo development builds with an official Expo plugin.
- The existing PulseSoc live backend already models rooms, viewers, chat, reactions, live feed posts, and replay handoff.
- LiveKit keeps real-time broadcasting separate from Mux, while Mux remains the archive/playback layer.

## Agora Notes

Agora is mature and production-ready, but using it now would require new dashboard setup, credentials, token service work, SDK integration, billing review, and a second realtime provider path. That adds launch risk tonight.

## Chosen Architecture

- VisionCamera: native preview and camera permission flow.
- LiveKit: realtime camera/microphone publishing and viewing.
- Mux: recording, replay processing, and Videos archive.
- PulseSoc backend: live sessions, feed LIVE post, chat, reactions, viewer count, notifications, and replay indexing.

