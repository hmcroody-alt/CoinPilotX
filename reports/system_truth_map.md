# System Truth Map

Generated: 2026-05-31

## Confirmed Production Truth

- `https://coinpilotx.app/` returned HTTP 200 with CSP present.
- Protected Pulse surfaces redirect to login instead of leaking content:
  - `/pulse`
  - `/pulse/reels`
  - `/pulse/messages`
  - `/pulse/live`
  - `/pulse/premium`
  - `/pulse/premium/undx`
- `/api/pulse/search?q=pulse` returned HTTP 401 when unauthenticated.
- `/pulse/search` returned HTTP 404. Search is currently verified as an in-app/API search flow, not as a standalone production page.

## Confirmed Local Truth

- Flask registers 835 routes locally.
- Local route groups include 66 `/pulse` page routes, 164 `/api/pulse` routes, 8 `/api/undx` routes, and 160 `/admin` routes.
- Local database initializes with 418 tables and 397 indexes.
- Full platform audit passed.
- Site functional audit passed.
- Performance audit passed with warnings only.
- Browser QA previously verified `/pulse`, `/pulse/reels`, `/pulse/messages`, `/pulse/live`, and `/pulse/premium/undx` on desktop and mobile viewport sizes with no horizontal overflow after the Reels fix.

## Confirmed Hardening Already Applied

- Media upload validation now checks signatures for images, GIFs, MP4/MOV/M4A, WebM, MP3, WAV, OGG, PDF, DOC, DOCX, and text files.
- Spoofed JPEG, MP4, PDF, and binary text uploads are covered by `scripts/media_integrity_audit.py`.
- Reels mobile overflow was fixed in `static/css/pulse_reels_experience.css` and the route-level Reels style in `bot.py`.
- `scripts/system_assessment_audit.py` validates core routes, database footprint, assessment reports, and upload spoofing controls.

## Blocked Truth

- Production authenticated Pulse UX could not be fully verified without a production login session.
- Production R2 object persistence could not be verified without Railway/R2 runtime credentials.
- Production live camera/mic/WebRTC/audio/replay behavior could not be verified without authenticated browser-device testing.
- Production worker heartbeat could not be verified from local database state.

## Current Platform Phase

Pulse is in foundation hardening and production-readiness verification. The social platform surface exists and local audits are broad, but Meta-scale readiness requires production media, live, worker, and latency proof.

