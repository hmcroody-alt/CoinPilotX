# PulseSoc Native Voice Messages

Date: 2026-07-18

## Root cause

The native recorder produced a valid local M4A URI and duration, but `ChatScreen` did not supply its byte size. `uploadMessengerMedia` converted the missing value to `0` and sent it to the production `/api/messages/media/init` contract. The existing private Messenger media foundation correctly rejected that declaration with `invalid_size` / `File size is required.`

This was a native metadata defect, not a missing backend or voice-message foundation.

## Repair

- Reused the production private pipeline: media init, multipart upload, completion, then Communications V2 message delivery with `attachment_ids`.
- Resolve the actual local file size through Expo FileSystem before media init whenever a picker or recorder does not provide it.
- Keep backend MIME, size-limit, membership, storage, checksum, processing, and attachment validation authoritative.
- Preserve native high-quality M4A recording and add live metering updates.
- Provide explicit discard and stop-and-send controls instead of overloading a single microphone button.
- Add a compact teal, cyan, violet, and coral waveform presentation for capture and playback.
- Retain playback pause/resume, duration, progress, and 1x/1.5x/2x speed controls.
- Replace the generic attachment alert title with voice-specific failure copy.

## Production contract

1. `POST /api/messages/media/init` with a positive `size_bytes`, `media_type=voice`, `audio/mp4`, and conversation ID.
2. `POST /api/messages/media/upload` with the returned attachment ID and private M4A file.
3. `POST /api/messages/media/complete` with duration metadata.
4. `POST /api/pulse/communications/v2/conversations/:id/messages` with the durable `attachment_ids` value.

No legacy uploader, public media URL, parallel voice database, or native-only message contract was added.

## Verification

- TypeScript typecheck: PASS
- Native voice-message contract audit: PASS
- Native Messenger audit: PASS
- `git diff --check`: PASS
- Simulator build and iPhone 16 Pro visual QA: PASS
- Simulator evidence: `reports/screenshots/native-voice-message-2026-07-18/voice-recording-dock.png`
- Physical iPhone 16 Pro signed build: PASS
- Development bundle: `com.pulsesoc.nativeapp.dev`
- Development display name: `PulseSoc Native Dev`
- Side-by-side installation and launch: PASS
- Production WebView bundle replaced or modified: NO
- Controlled production voice delivery: requires the authenticated physical-device retest after installation
