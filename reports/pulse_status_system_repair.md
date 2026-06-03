# Pulse Status System Repair

Date: 2026-06-03

## Repairs

- The Status media picker is no longer disabled in text mode.
- Choosing media automatically switches the Status type to photo or video.
- Text, photo, video, and mixed Status posts continue through the same `/api/pulse/media/upload` and `/api/pulse/status` flow.
- Live preview updates from text, media, and style controls.
- Recent Status reloads after successful posting and highlights the new card.
- Home Status cards now show visible content before opening.
- Video cards loop a muted teaser preview for up to the first seconds to invite opening.
- Status cards open a full viewer with media, text, creator, progress, next, previous, and close controls.

## Playback

Status media still prefers Mux HLS when `mux_playback_id` is present:

`https://stream.mux.com/{mux_playback_id}.m3u8`

Raw R2/CDN playback remains a fallback for non-video media or processing states.

## Audit

`scripts/pulse_status_upload_viewer_audit.py` verifies upload, preview, style, viewer, and media-picker behavior.
