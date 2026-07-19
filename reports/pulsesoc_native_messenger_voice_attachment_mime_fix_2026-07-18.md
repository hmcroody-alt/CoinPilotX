# PulseSoc Native Messenger Voice Attachment MIME Fix

Date: 2026-07-18

## Symptom

Native Pulse Command showed:

- `Voice message failed`
- `Uploaded file type does not match the initialized attachment.`

The same error was also surfaced in the composer status panel after attempting a voice message.

## Root Cause

The native Messenger voice path correctly used the existing production media foundation:

1. `POST /api/messages/media/init`
2. `POST /api/messages/media/upload`
3. `POST /api/messages/media/complete`
4. `POST /api/pulse/communications/v2/conversations/<conversation_id>/messages` with `attachment_ids`

However, iOS simulator/device multipart uploads can report recorded M4A files as `audio/x-m4a`, `audio/m4a`, `audio/mp4a-latm`, or `application/octet-stream` even when the native code initialized the server attachment as `audio/mp4`.

The backend foundation compared the uploaded multipart MIME to the initialized MIME exactly, so a valid iOS voice recording could be rejected before completion.

## Fix

- `mobile-native/src/api/messenger.ts`
  - Canonicalizes iOS voice recording MIME aliases to `audio/mp4` before initializing the durable Messenger attachment.
  - Keeps the final Messenger `message_type` as `voice`, preserving production message rendering.

- `services/messenger_media_foundation.py`
  - Adds server-side MIME alias normalization for M4A voice recordings.
  - Treats multipart `application/octet-stream` as the already-initialized MIME only after the server-created attachment record exists and the expected MIME is supported.

- `scripts/pulsesoc_native_voice_message_audit.py`
  - Guards the native and backend MIME normalization contract.

## Preserved Behavior

- No duplicate upload provider was added.
- No duplicate Messenger pipeline was added.
- Existing `attachment_ids` delivery remains authoritative.
- Existing voice message rendering remains `message_type: voice`.
- Existing image/video/file attachment behavior remains on the same foundation.

## QA Status

- Code-path verified.
- Audit added for this specific failure.
- TypeScript passed.
- Expo Doctor passed 17/17.
- Python compile passed for `services/messenger_media_foundation.py`.
- Xcode iPhone Simulator Release build/install/launch passed.
- Simulator install evidence: `reports/screenshots/native-messenger-voice-attachment-mime-2026-07-18/simulator-after-patched-install.png`.
- Real-account conversation voice retry remains the final runtime confirmation because it requires recording and sending from the user's authenticated conversation.
