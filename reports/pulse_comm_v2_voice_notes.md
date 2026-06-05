# Pulse Communications V2 Phase 2 Voice Notes

Phase 2 delivers voice notes only. It does not begin audio calling, video calling, WebRTC, TURN/STUN, or group calling.

## Implemented

- Communications V2 composer includes a microphone recorder.
- Recorder supports permission request, start, pause, resume, stop, preview, discard, and send.
- Recording UI shows state, timer, waveform, pause state, and ready-to-send state.
- Voice notes upload through `/api/pulse/communications/v2/attachments/upload`.
- Voice uploads include duration, size, MIME type, waveform metadata, creator user ID, and conversation ID.
- Voice notes are sent as normal `message_type=voice` messages with an audio attachment.
- Playback supports play, pause, seek/progress, duration, waveform, and speeds: 1x, 1.5x, 2x.
- Voice attachments render in direct messages, groups, and rooms through the same message history path.

## Backend Safety

- Auth is required for upload and send routes.
- Conversation access is checked before staging voice uploads.
- Voice uploads validate MIME type, extension, duration, and file size.
- Voice metadata is stored on `chat_media_uploads` and copied into `comm_v2_attachments`.
- Default limits: `COMM_V2_VOICE_MAX_SECONDS=300`, `COMM_V2_VOICE_MAX_MB=15`.

## Phase Gate

Phase 2 ends at voice notes. Future phases remain documented but inactive.
