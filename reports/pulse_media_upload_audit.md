# Pulse Media Upload Audit

Generated: 2026-06-02T18:21:22

## Infrastructure

- Storage provider: `local`
- Storage configured: `True`
- R2/CDN base: `not configured locally`
- Mux configured: `False`
- ffmpeg present: `False`

## Upload Results

| Upload type | Result | Media type | URL | CDN | Mux playback | Processing | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| image-only | PASS | image / image/png | `/static/uploads/pulse_media/2026/06/02/pulse-image-e1e7cc314f4efb40.png` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only webm | PASS | video / video/webm | `/static/uploads/pulse_media/2026/06/02/pulse-video-9318a38f2b64dfd4.webm` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only mov | PASS | video / video/quicktime | `/static/uploads/pulse_media/2026/06/02/pulse-video-c798c49798cf1320.mov` | `n/a` | `n/a` | ready/verified | MOV stored; playback may vary until transcoding is enabled. |
| audio-only | PASS | audio / audio/ogg | `/static/uploads/pulse_media/2026/06/02/pulse-audio-a5e3d812c10251e5.ogg` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |

## Created Objects

- Mixed media Pulse post: `848`
- Photo/video/audio Status ids: `597, 598, 599`
- Reel id: `65`
- Original sound track id: `26246`

## Resolution

- Pulse media upload returns readable JSON with `success`, `media_url`, and `status_id`.
- Image, MP4/MOV/WebM video, and audio files are accepted by the current upload path.
- Mux playback ids are preserved when present; no Mux id is fabricated when local/R2 upload does not create one.
- Media engine failure states remain safe through `pending_unavailable` and `processing_blocked` worker handling.
