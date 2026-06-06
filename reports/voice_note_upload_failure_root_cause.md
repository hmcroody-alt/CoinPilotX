# Voice Note Upload Failure Root Cause

## Observed

Voice note records successfully, then send fails with:

`This media file could not be verified safely.`

## Root Cause

The voice recording frontend used the recorded blob MIME but generated an extension that did not always match the actual container.

Example problematic path:

- recorded MIME: `audio/webm`
- generated filename extension: `.ogg`
- backend safety verifier checked Ogg header
- actual WebM/EBML header did not match Ogg
- upload rejected with safe verification failure

Another related issue:

- backend upload classification checked `.webm` as video before considering `audio/webm`
- voice notes could be staged as video-like media instead of audio

## Fix

- Frontend now maps voice filename extension from actual MIME:
  - `audio/webm` -> `.webm`
  - `audio/ogg` -> `.ogg`
  - `audio/mp4` / `audio/m4a` -> `.m4a`
  - `audio/aac` -> `.aac`
- Backend now prioritizes audio MIME detection.
- Backend accepts Safari/Android/desktop audio MIME variants.
- Header rejection logs now include safe diagnostic data: extension, MIME, size, and header prefix.

## Validation

- `scripts/pulse_voice_upload_audit.py`: PASS
- `scripts/pulse_voice_security_audit.py`: PASS
