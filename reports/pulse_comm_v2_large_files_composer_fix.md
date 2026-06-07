# Pulse Communications V2 Large Files and Composer Fix

Date: 2026-06-07

## Issue

Mobile Communications V2 rejected normal phone media as too large, allowed voice recordings to fall into an unsupported-format state, and showed attachment/voice error panels in a way that made the bottom composer feel cramped.

## Fix

- Raised Communications V2 attachment limits without changing the regular Pulse feed/status media limits.
- Added a dedicated request-size allowance for `/api/pulse/communications/v2/attachments/upload`.
- Kept media-only messages valid so users can send a file, image, video, or voice note with no typed text.
- Made recorded voice notes prefer mobile-friendly formats and avoid false rejection from strict header checks.
- Improved the mobile composer so attach, type, voice, and send stay in one easy row.
- Kept attachment previews compact, with retry/remove controls and non-blocking error status above the composer.

## Limits

Defaults are configurable by environment:

- `COMM_V2_IMAGE_MAX_MB`: 100 MB
- `COMM_V2_AUDIO_MAX_MB` / `COMM_V2_VOICE_MAX_MB`: 100 MB
- `COMM_V2_VIDEO_MAX_MB`: 1024 MB
- `COMM_V2_FILE_MAX_MB`: 1024 MB
- `PULSE_COMM_V2_MAX_REQUEST_MB`: defaults to `COMM_V2_FILE_MAX_MB`

## Guardrails

Added `scripts/pulse_comm_v2_large_file_composer_audit.py` to verify:

- the large upload route is not trapped by the generic small POST cap
- frontend and backend limits agree
- attachment-only send is permitted
- recorded voice notes avoid false format rejection
- mobile composer controls remain easy to reach

