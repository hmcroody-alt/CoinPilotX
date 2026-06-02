# Pulse Media Upload Audit

Generated: 2026-06-02T01:25:01

## Infrastructure

- Storage provider: `local`
- Storage configured: `True`
- R2/CDN base: `not configured locally`
- Mux configured: `False`
- ffmpeg present: `False`

## Upload Results

| Upload type | Result | Media type | URL | CDN | Mux playback | Processing | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| image-only | PASS | image / image/png | `/static/uploads/pulse_media/2026/06/02/pulse-image-27a486636fc1187c.png` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only webm | PASS | video / video/webm | `/static/uploads/pulse_media/2026/06/02/pulse-video-805a0717e5e07481.webm` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |
| video-only mov | PASS | video / video/quicktime | `/static/uploads/pulse_media/2026/06/02/pulse-video-f6bbb7b3595bb88e.mov` | `n/a` | `n/a` | ready/verified | MOV stored; playback may vary until transcoding is enabled. |
| audio-only | PASS | audio / audio/ogg | `/static/uploads/pulse_media/2026/06/02/pulse-audio-eb4d698254cc6c78.ogg` | `n/a` | `n/a` | ready/verified | Uploaded and normalized. |

## Created Objects

- Mixed media Pulse post: `841`
- Photo/video/audio Status ids: `560, 561, 562`
- Reel id: `62`
- Original sound track id: `26132`

## Resolution

- Pulse media upload returns readable JSON with `success`, `media_url`, and `status_id`.
- Image, MP4/MOV/WebM video, and audio files are accepted by the current upload path.
- Mux playback ids are preserved when present; no Mux id is fabricated when local/R2 upload does not create one.
- Media engine failure states remain safe through `pending_unavailable` and `processing_blocked` worker handling.
