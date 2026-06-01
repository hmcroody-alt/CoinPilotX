# Pulse Media and Reels Audit

Date: 2026-06-01

## Scope

Audited Pulse media surfaces and shared media plumbing for:

- Pulse Reels
- Pulse feed media
- Pulse Status media
- Profile media references
- Messenger media references
- Group media references
- Shared upload, storage, and renderer components

## Files Reviewed

- `bot.py`
- `services/media_service.py`
- `services/media_storage.py`
- `static/js/pulse_media_renderer.js`
- `static/css/pulse_reels_experience.css`
- `media_worker.py`
- Existing media and Reels audit scripts under `scripts/`

## Findings

### Reels Playback

The Reels page already used a vertical shell and canonical media rendering, but the active playback logic was too blunt:

- All videos in a visible card could be touched by the same playback pass.
- Sound state was held only as a volatile `reelsMuted` boolean.
- Sound preference was not persisted.
- Browser autoplay/audio blocking did not fall back cleanly to muted playback.
- Stall/waiting events were logged, but recovery behavior was not explicit enough.
- Progress UI and direct play/sound controls were missing from the Reels card.

### Audio

Muted autoplay is the safest default for Reels. The old click behavior toggled mute by tapping the media area, which made audio state easy to change accidentally and did not persist user intent.

### Shared Media Contract

The canonical media resolver already contained most required fields, including Mux readiness helpers, but public payloads did not consistently expose all future-ready fields to frontends:

- `playback_url`
- `mux_playback_id`
- `mux_hls_url`
- `mux_thumbnail_url`
- `duration`
- `has_audio`
- `created_at`

The shared renderer also needed DOM data attributes for these fields.

### Mux Readiness

Mux playback should prefer:

- HLS: `https://stream.mux.com/{PLAYBACK_ID}.m3u8`
- Thumbnail: `https://image.mux.com/{PLAYBACK_ID}/thumbnail.jpg`

Direct CDN/MP4 fallback must remain available when Mux is absent or native HLS is not supported by the browser.

### ffmpeg / Worker Readiness

The media worker had ffmpeg presence checks, but video jobs could still complete without making the missing ffmpeg state clear enough. On Railway, ffmpeg should be installed with:

`RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg`

If ffmpeg is missing, video jobs should be visible as blocked/unavailable rather than silently successful.

## Changes Implemented

- Added persisted Reels sound preference using `pulseReelsSoundEnabled`.
- Added explicit Reels play/pause and sound buttons.
- Added a slim Reels progress bar.
- Added a small sound badge that shows `Tap for sound`, `Sound on`, or `No audio track`.
- Changed playback control so only the active visible Reel plays.
- Paused offscreen videos and kept nearby video preload lightweight.
- Added stall/wait retry scheduling for Reels media.
- Gated noisy video diagnostics behind local/debug mode while keeping error diagnostics available.
- Expanded shared media normalization and DOM attributes for Mux, CDN, duration, audio, and creation metadata.
- Added Mux-safe playback URL and thumbnail generation.
- Added admin media health diagnostics for ffmpeg, Mux env presence, worker heartbeat, last processed media, and last error.
- Updated media worker behavior so missing ffmpeg marks video jobs as `pending_unavailable` and uploads as `processing_blocked`.

## Safety Notes

- No secrets are logged or exposed.
- Mux diagnostics show only whether env vars are configured.
- Reels diagnostics do not log message bodies or private media contents.
- Direct CDN/MP4 playback remains supported.
- Existing Pulse feed, Status, Messenger, and Groups media references continue to use the shared resolver/renderer.
- No destructive database changes were made.

## New Audits

- `scripts/pulse_reels_media_audit.py`
- `scripts/pulse_media_surface_audit.py`

## Remaining Production Checks

- Confirm production Railway has durable storage configured for Pulse media.
- Confirm `MUX_TOKEN_ID` and `MUX_TOKEN_SECRET` are configured only if Mux processing is enabled.
- Confirm Railway includes `RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg` for video processing jobs.
- Confirm real uploaded videos have range-supporting CDN responses.

## Rollback

Rollback is straightforward because changes are additive and non-destructive:

- Revert Reels page playback/control changes in `bot.py`.
- Revert shared renderer normalization additions in `static/js/pulse_media_renderer.js`.
- Revert media resolver public field additions in `services/media_service.py`.
- Revert worker blocked-status logic in `media_worker.py`.

No production data deletion or schema-destructive migration was introduced.
