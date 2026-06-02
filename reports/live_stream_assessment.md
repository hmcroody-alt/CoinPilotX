# Live Stream Assessment

Generated: 2026-05-31

## Pipeline Map

Publisher camera/mic -> browser getUserMedia -> Pulse Live API -> live session row -> WebRTC signaling table -> peer connections -> viewer video/audio element -> HLS fallback -> archive/replay state -> feed/profile/replay distribution.

## Confirmed Locally

- Live pipeline audit passed.
- Live start creates live session and feed post.
- WebRTC signaling endpoints exist.
- Browser transport code creates peer connections.
- Publisher diagnostics inspect audio and video tracks.
- Viewer code attaches remote media stream.
- Viewer page exposes audio unlock.
- Replay lifecycle audit passed for available/unavailable replay states.
- Live chat, reactions, scenes, restream, multistream isolation, and mobile contracts passed local audits.

## Confirmed Code Risk

- `static/js/pulse_live_studio.js` configures STUN servers only:
  - `stun:stun.l.google.com:19302`
  - `stun:stun.cloudflare.com:3478`
- TURN relay configuration is not confirmed.

## Root Cause Candidates for "No Sound"

Confirmed:
- Browser audio unlock UI exists.
- Audio track state is persisted in local audits.
- STUN-only WebRTC is confirmed.

Blocked:
- Production selected ICE candidate type, audio bytes received, muted autoplay state, and device permission state were not measured in this pass.

Most important verified weakness:
- Without TURN, WebRTC may fail for viewers behind restrictive NAT/firewalls. This can look like "connected but no audio/video."

## Root Cause Candidates for "Replays Disappear"

Confirmed:
- Replay schema fields exist.
- Replay available/unavailable states exist.
- Local replay lifecycle audit passes.

Blocked:
- Production durable recording assets and R2/CDN persistence were not verified.

Most important verified weakness:
- Replay durability depends on media worker, durable storage, and production recording pipeline health. Local worker heartbeat is stale and production worker health must be verified.

## Required Next Fixes

1. Add production TURN configuration.
2. Add live diagnostics for selected candidate pair, relay/STUN/local candidate type, audio bytes, video bytes, and remote track mute state.
3. Run publisher/viewer QA on two networks and two device classes.
4. Run replay persistence QA through R2/CDN.

