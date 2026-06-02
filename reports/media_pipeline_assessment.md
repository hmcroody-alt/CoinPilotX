# Media Pipeline Assessment

Generated: 2026-05-31

## Pipeline Map

User upload -> `/api/pulse/media/upload` -> media validation -> `services/media_service.py` -> storage adapter -> local or R2 object -> `chat_media_uploads` row -> canonical resolver -> feed/reels/status/messenger/profile rendering -> retry/fallback UI.

## Confirmed

- Local media attachment audit passed image and video upload flows.
- Upload progress states are present.
- Canonical media resolver exists and is used by Pulse rendering paths.
- No recently visible local media resolved to raw `/Users/` paths in the media integrity audit.
- CDN canonicalization logic maps storage keys to public CDN URLs when R2 is active.
- Upload signature validation now covers image, GIF, video, audio, PDF, DOC, DOCX, and text categories.
- Reels pipeline audit passed.
- Cross-device media audit passed.

## Blocked

- Production R2 upload could not be verified from local environment.
- Production CDN cache headers and object content-types require Railway/R2 credentials or an authenticated production smoke.
- Production playback across iOS Safari, Android Chrome, and desktop browsers requires real device QA.

## Risks

- P0 if R2 variables are missing or attached to the wrong Railway service.
- P1 if media worker is stale in production.
- P1 if ffmpeg is unavailable in production.
- P2 if large videos are served without transcoding/thumbnail variants.

## Required Next Proof

1. Run R2 upload smoke test in Railway.
2. Upload JPEG, PNG, WebP, GIF, MP4, WebM, MOV, MP3, WAV, and PDF test files.
3. Confirm database rows, CDN URLs, MIME/content-type headers, playback, thumbnails, and retry behavior.
4. Confirm no private R2 URLs or raw local paths render publicly.

