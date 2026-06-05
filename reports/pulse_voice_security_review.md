# Pulse Voice Notes Security Review

## Controls

- Voice upload routes require an authenticated Pulse account.
- Upload staging checks conversation access before accepting a conversation-scoped recording.
- MIME validation accepts only audio-like recording formats.
- Extension validation blocks non-audio uploads passed as voice notes.
- Duration and size limits prevent abuse and accidental oversized recordings.
- Voice notes reuse the existing durable media pipeline and ownership checks.
- Final message send still requires valid conversation membership.

## Abuse Prevention

- Voice notes are normal Communications V2 attachments, so moderation/report/block flows still apply.
- Upload metadata is sanitized and waveform arrays are capped before storage.
- Secrets and private media credentials are not exposed in logs or payloads.

## Out Of Scope For Phase 2

Audio calling, video calling, WebRTC signaling, TURN/STUN credential issuance, and group calls remain unimplemented until later phases.
