# PulseSoc Livestream Golden Path

PulseSoc livestreaming is a protected production system. Do not remove or replace LiveKit, Mux, RTMP, HLS, WebRTC, egress, viewer playback, host preview, or stream health code without explicit approval and passing protection checks.

## Data Flow

Host browser camera and microphone publish to a LiveKit room. The backend creates or reuses the live session room, waits for stable host tracks, then starts LiveKit egress to the Mux RTMP ingest. Public viewers receive Mux HLS playback only. Stream keys and RTMP ingest credentials stay host/admin-only.

## Required Safety Contracts

- Host studio preview is always muted with volume zero to prevent echo.
- Viewer playback uses Mux HLS when Mux is active.
- LiveKit room-composite egress remains the preferred path, with fallback handling.
- Egress quota exhaustion is surfaced as a friendly failure and should not break the local camera preview.
- `Source closed` is treated as a track stability or egress failure and must be diagnosed before claiming livestream is healthy.
- Stream keys, API keys, webhook secrets, and private credentials are never printed or exposed to public viewers.

## Health States

- Green: LiveKit room connected, host participant present, video/audio tracks published, egress active, Mux active, playback healthy.
- Yellow: camera local only, egress pending, Mux idle, playback waiting, or quota warning.
- Red: room disconnected, track lost, egress failed, Mux inactive after egress, source closed, viewer playback failed.

## Recovery Flow

- If LiveKit disconnects, reconnect the host session and preserve the local camera preview.
- If egress fails from quota, keep browser live local/LiveKit-direct and display a clear quota message.
- If egress dies for transient reasons, stop stale egress and attempt one safe restart after tracks are stable.
- If viewer HLS fails, retry playback and show a short actionable message.

## Deployment Gate

Every deployment must run `python scripts/protection/run_protection_suite.py`. Real deployed live-stream QA is still required before declaring LiveKit-to-Mux production health.
