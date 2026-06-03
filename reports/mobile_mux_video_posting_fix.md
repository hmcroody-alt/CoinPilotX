# Mobile Mux Video Posting Fix

## Summary

Mobile feed publishing showed the misleading message:

`Pulse is warming up. Create the first post. Tap publish to retry.`

That copy came from the shared Pulse frontend API helper. It was being used as a generic fallback for non-feed request failures, including video upload or post publish failures. The message now only applies to feed-empty/feed-loading responses.

## Mux Credential Detection

The media service now reports and logs safe booleans only:

- `MUX_TOKEN_ID_loaded`
- `MUX_TOKEN_SECRET_loaded`
- `MUX_WEBHOOK_SECRET_loaded`
- `MUX_DATA_ENV_KEY_loaded`
- `MUX_DATA_ENV_KEY_used`

`MUX_DATA_ENV_KEY` remains ignored unless `MUX_DATA_ANALYTICS_ENABLED=true`.

## Upload Pipeline

For mobile video posts, the intended flow is:

1. Upload original media to the existing Pulse media endpoint.
2. Store durable media in R2/CDN or configured media storage.
3. Create a Mux asset from the public durable URL when Mux credentials are configured.
4. Store `mux_asset_id`, `mux_playback_id`, and `mux_status`.
5. Create the Pulse post using the returned media ID.
6. Show a processing state while Mux HLS is preparing.
7. Play from `https://stream.mux.com/{mux_playback_id}.m3u8` when ready.

## Fixes Applied

- Separated feed-empty text from upload/publish failure text in the Pulse API helper.
- Added safe Mux diagnostics logging in `services/media_service.py`.
- Added `MUX_WEBHOOK_SECRET` and `MUX_DATA_ENV_KEY` diagnostics support.
- Added explicit Mux processing state to media payloads.
- Updated shared and inline media renderers to show `Video is processing.` when Mux has accepted the asset but HLS is not ready.
- Expanded `/api/pulse/live/mux/webhook` handling for `video.asset.ready` and `video.asset.errored` so normal uploaded media rows are updated, not only live replay rows.

## Browser QA Note

The in-app browser refused the production URL under its browser security policy during this run, so authenticated mobile network capture could not be completed from this environment. The code-level root cause and validation checks were completed locally.

## Expected Production Evidence After Deploy

- `/api/pulse/media/upload` returns JSON with a media ID for mobile MP4 upload.
- Railway logs include `PULSE_MUX_ENV_DIAGNOSTICS` with booleans only.
- Railway logs include `PULSE_MUX_ASSET_CREATED`.
- `chat_media_uploads.mux_asset_id` is populated.
- `chat_media_uploads.mux_playback_id` is populated.
- `chat_media_uploads.mux_status` starts as created/preparing and becomes ready after webhook.
- `chat_media_uploads.playback_url` becomes `https://stream.mux.com/{playback_id}.m3u8`.
- The published post appears immediately; if Mux is still preparing, the card says `Video is processing.`

## Remaining Production QA

- Mobile text post.
- Mobile image post.
- Mobile MP4 video post.
- Mux asset creation in Railway logs.
- Mux ready webhook updates.
- Feed playback from Mux HLS.
- Desktop video regression.
- Reels mobile playback.
- Status mobile video playback.
