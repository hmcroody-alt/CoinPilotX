# Pulse Media Upload Audit

Generated: 2026-07-26T00:15:49

## Infrastructure

- Storage provider: `local`
- Storage configured: `True`
- R2/CDN base: `not configured locally`
- Mux configured: `False`
- ffmpeg present: `True`

## Upload Results

| Upload type | Result | Media type | URL | CDN | Mux playback | Processing | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| image-only | PASS | image / image/png | `/static/uploads/pulse_media/2026/07/26/pulse-image-3348392b6e80084f.png` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only webm | PASS | video / video/webm | `/static/uploads/pulse_media/2026/07/26/pulse-video-c59a3378104e944f.webm` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only mov | PASS | video / video/quicktime | `/static/uploads/pulse_media/2026/07/26/pulse-video-8727668101a27d47.mov` | `n/a` | `n/a` | ready/verified | MOV stored; playback may vary until transcoding is enabled. |
| audio-only | PASS | audio / audio/ogg | `/static/uploads/pulse_media/2026/07/26/pulse-audio-c53ef7cd53ce2f36.ogg` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |

## Created Objects

- Mixed media Pulse post: `1`
- Photo/video/audio Status ids: `1, 2, 3`
- Reel id: `1`
- Original sound track id: `4`

## Resolution

- Pulse media upload returns readable JSON with `success`, `media_url`, and `status_id`.
- Image, MP4/MOV/WebM video, and audio files are accepted by the current upload path.
- Mux playback ids are preserved when present; no Mux id is fabricated when local/R2 upload does not create one.
- Media engine failure states remain safe through `pending_unavailable` and `processing_blocked` worker handling.
