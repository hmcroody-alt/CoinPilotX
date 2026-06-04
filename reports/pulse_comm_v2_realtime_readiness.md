# Pulse Communications V2 Realtime Readiness

## Current Truth

Communications V2 already supports authenticated conversations, message sending, R2-backed attachment upload, image/file/video message rendering, and Mux-preferred video playback. The voice and video call endpoints are intentionally placeholders; they do not create a real call.

## Voice Messages

Available foundation:

- Message types already include audio and voice.
- The attachment table can store media metadata.
- The authenticated attachment upload route is working foundation.

Missing before voice messages are production-ready:

- Browser `MediaRecorder` flow with microphone permission, record, pause, stop, preview, discard, and send.
- Voice-note duration and waveform metadata.
- Audio normalization/transcoding and mobile Safari validation.
- Compact voice-note playback UI, upload progress, retry, and failure state.
- Moderation, file-size, MIME, and duration enforcement.

## Media Sharing

Available foundation:

- Authenticated attachment upload.
- R2 storage metadata and Mux fields.
- Image, file, and video message rendering.
- Message payload support for attached media IDs.

Missing before media sharing feels complete:

- A polished attachment sheet with camera, photo, video, file, and voice choices.
- Multiple-attachment preview, remove/reorder, upload progress, cancel, retry, and resumable large uploads.
- Production validation for R2 delivery, private visibility, virus scanning, moderation, and retention rules.
- Clear attachment limits and mobile device QA.

## Audio And Video Calls

The current voice/video start routes return a Phase 2 placeholder. Twilio in this repository is currently used for SMS notifications, not call media or TURN.

Missing before calls can work:

- WebRTC call session and participant models.
- A realtime signaling transport such as WebSocket or Socket.IO.
- Production TURN/STUN credentials and short-lived credential issuance.
- Ringing, invite, accept, decline, end, reconnect, timeout, and busy states.
- Camera/microphone permissions, device selection, participant tracks, and call UI.
- Incoming-call notifications, call history, moderation, abuse controls, and quality metrics.
- Mobile background/interruption QA and a privacy/security review.

Mux Live can power creator broadcasts and replays, but it is not a replacement for low-latency one-to-one or group WebRTC calls.

## Safe Implementation Order

1. Finish media sharing UX and production R2 validation.
2. Add voice-note recording and playback using the existing attachment pipeline.
3. Add realtime presence and WebSocket signaling.
4. Configure and validate TURN/STUN with short-lived credentials.
5. Ship one-to-one audio calls behind the V2 feature flag.
6. Add video calls, then group calls, with staged mobile and security testing.
