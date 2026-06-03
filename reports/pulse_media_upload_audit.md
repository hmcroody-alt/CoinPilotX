# Pulse Media Upload Audit

Generated: 2026-06-03T12:17:45

## Infrastructure

- Storage provider: `local`
- Storage configured: `True`
- R2/CDN base: `not configured locally`
- Mux configured: `False`
- ffmpeg present: `False`

## Upload Results

| Upload type | Result | Media type | URL | CDN | Mux playback | Processing | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| image-only | PASS | image / image/png | `/static/uploads/pulse_media/2026/06/03/pulse-image-92196cfa47cfb836.png` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only webm | PASS | video / video/webm | `/static/uploads/pulse_media/2026/06/03/pulse-video-d6e6fd55d50fcf8f.webm` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only mov | PASS | video / video/quicktime | `/static/uploads/pulse_media/2026/06/03/pulse-video-16526360db17ad8e.mov` | `n/a` | `n/a` | ready/verified | MOV stored; playback may vary until transcoding is enabled. |
| audio-only | PASS | audio / audio/ogg | `/static/uploads/pulse_media/2026/06/03/pulse-audio-881120ec3f050dca.ogg` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |

## Created Objects

- Mixed media Pulse post: `856`
- Photo/video/audio Status ids: `627, 628, 629`
- Reel id: `70`
- Original sound track id: `26773`

## Resolution

- Pulse media upload returns readable JSON with `success`, `media_url`, and `status_id`.
- Image, MP4/MOV/WebM video, and audio files are accepted by the current upload path.
- Mux playback ids are preserved when present; no Mux id is fabricated when local/R2 upload does not create one.
- Media engine failure states remain safe through `pending_unavailable` and `processing_blocked` worker handling.
