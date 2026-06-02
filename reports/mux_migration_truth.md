# Mux Migration Truth

Date: 2026-06-02

## Summary

Mux is now the preferred playback path for Pulse user-facing videos when a Mux playback id exists and the asset is ready.

Preferred playback URL:

`https://stream.mux.com/{playback_id}.m3u8`

Fallback playback remains available through the existing first-party/R2 stream only while Mux is not configured, no playback id exists, or the Mux asset is still processing.

## Are Uploads Reaching Mux?

Code path after this change:

1. `/api/pulse/media/upload` receives the authenticated upload.
2. `services/upload_progress_service.py` delegates to `services/media_service.save_upload`.
3. `save_upload` stores the original object in durable media storage first.
4. For video uploads only, if `MUX_TOKEN_ID` and `MUX_TOKEN_SECRET` are configured, `services.media_service.create_mux_asset_from_url` calls the Mux Assets API with the public durable media URL.
5. Mux asset creation failures are logged safely and do not block the upload; playback falls back to the existing R2/first-party stream until Mux is available.

Result: video uploads are expected to reach Mux when the production Mux environment variables are present and Mux can fetch the durable public input URL.

## Are Playback IDs Stored?

The primary media table now stores:

- `mux_asset_id`
- `mux_playback_id`
- `mux_status`

Existing fields still used:

- `playback_url`
- `playback_storage_key`
- `playback_mime_type`

The secondary `pulse_media_assets` index now also stores:

- `mux_asset_id`
- `mux_playback_id`
- `mux_status`
- `playback_url`

## Which Player Is Currently Used?

The shared browser player is `static/js/pulse_media_renderer.js`.

After this change it:

- prefers `playback_url` / Mux HLS when present
- supports native HLS browsers directly
- loads HLS.js for non-native HLS browsers
- falls back to existing media URLs if Mux is unavailable

Surfaces using the shared playback path:

- Feed videos
- Reels
- Status viewer videos

## Which Routes Still Use R2?

R2 is still used for durable upload/source storage and fallback delivery:

- `POST /api/pulse/media/upload`
- `GET /api/pulse/media/<id>/stream`

R2 should no longer be the primary user-facing playback path when a ready Mux playback id exists.

Fallback cases that still use R2/first-party streaming:

- Mux is not configured
- Mux asset creation failed
- Mux asset status is not ready yet
- Existing legacy video rows do not have `mux_playback_id`

## What Remains Before Feed/Reels/Status Run Fully On Mux?

1. Deploy this migration to production.
2. Verify new uploads populate `mux_asset_id`, `mux_playback_id`, and `mux_status`.
3. Confirm Mux transitions video assets to `ready`.
4. Backfill existing video rows that already live in R2 but do not yet have Mux asset/playback ids.
5. Verify production browser playback on:
   - Feed video
   - Reels video
   - Status video
   - Safari
   - Chrome
   - Mobile browsers
6. Confirm production no longer renders raw `.mov` CDN URLs as the primary video source when Mux playback exists.

## Exact Migration Fix Implemented

- `services/media_service.py`
  - added Mux asset creation from durable public upload URLs
  - added Mux playback URL resolution with R2 fallback while Mux is not ready
  - exposes `mux_asset_id`, `mux_playback_id`, `mux_status`, and `playback_mime_type`

- `services/pulse_feed_engine.py`
  - preserves Mux fields in canonical feed media payloads

- `static/js/pulse_media_renderer.js`
  - prefers Mux HLS playback
  - adds HLS.js support for browsers without native HLS playback

- `bot.py`
  - stores Mux metadata in database schema and media asset index
  - Feed videos prefer `playback_url`/Mux HLS before raw R2/CDN URLs
  - Reels and Status pass HLS playback MIME metadata to the shared renderer

## Production Verification Needed

Production DB/R2/Mux verification requires deployed code plus production credentials/log access.

Expected successful upload evidence:

- `PULSE_MUX_ASSET_CREATED`
- `mux_asset_id` populated
- `mux_playback_id` populated
- `mux_status=ready` once Mux processing completes
- rendered video source starts with `https://stream.mux.com/`

