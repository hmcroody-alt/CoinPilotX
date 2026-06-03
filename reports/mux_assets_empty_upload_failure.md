# Mux Assets Empty Upload Failure

Generated: 2026-06-02

## Scope

Fresh Pulse video upload path only:

- `POST /api/pulse/media/upload`
- Reels final publish handoff after upload
- Mux asset creation from the durable public media URL

Playback UI, old videos, and layout changes were intentionally left out of scope.

## Endpoint

Fresh Reel uploads first call:

```text
POST /api/pulse/media/upload
multipart/form-data
file=<video>
context_type=pulse_reel
context_id=draft
```

After a successful media upload, the browser calls:

```text
POST /api/pulse/reels/create
application/json
media_id=<uploaded media id>
media_ids=[<uploaded media id>]
```

## Root Cause

Before this fix, video upload could reach durable storage and still return a successful upload response even when Mux asset creation did not produce:

- `mux_asset_id`
- `mux_playback_id`
- `mux_status`

That left Mux Assets empty and made the final Reels publish path depend on a media row that had no Mux playback asset. The service also logged only a generic Mux failure, so production could not easily distinguish:

- Mux credentials missing from the web service
- credentials present but rejected by Mux
- missing public HTTPS input URL
- Mux API network/HTTP failure
- Mux response missing a playback id

## Fix Applied

`services/media_service.py`

- Added safe Mux diagnostics:
  - `mux_token_id_present`
  - `mux_token_secret_present`
  - `mux_asset_create_attempt`
  - `mux_asset_create_status`
  - `mux_asset_create_status_code`
  - `mux_asset_create_error_type`
- Normalized Railway env values with surrounding quotes stripped.
- Added explicit `PULSE_MUX_ASSET_CREATE_ATTEMPT` logging before the Mux API request.
- Added HTTP status/error classification for Mux API failures.
- Added `PULSE_MUX_ASSET_REQUIRED_FAILED` logging when video upload reaches storage but Mux asset creation fails.
- For configured Mux video uploads, returns a clear 502 instead of a false-success upload when asset creation fails.
- Successful video uploads continue to store:
  - `mux_asset_id`
  - `mux_playback_id`
  - `mux_status`
  - HLS `playback_url`

`bot.py`

- Pulse media asset index now stores the real media `processing_status` instead of forcing every upload to `ready`.

`scripts/mux_migration_audit.py`

- Expanded to verify the Mux diagnostics and required-failure behavior exist.

## Expected Production Evidence After Deploy

Successful fresh video upload should show logs like:

```text
PULSE_MEDIA_UPLOAD_ROUTE_HIT ...
PULSE_MEDIA_UPLOAD_STORAGE_RESULT ... durable_uploaded=True ...
PULSE_MUX_ASSET_CREATE_ATTEMPT ... mux_asset_create_attempt=True ...
PULSE_MUX_ASSET_CREATED ... mux_asset_id=... mux_playback_id=... mux_status=...
PULSE_MEDIA_UPLOAD_COMPLETE ... mux_asset_create_attempt=True ...
```

The upload response should include:

```json
{
  "ok": true,
  "success": true,
  "media": {
    "id": 123,
    "mux_asset_id": "...",
    "mux_playback_id": "...",
    "mux_status": "preparing",
    "playback_url": "https://stream.mux.com/<playback_id>.m3u8",
    "processing_status": "mux_processing"
  }
}
```

If Mux still fails, the response should be JSON with a clear error:

```json
{
  "ok": false,
  "error": "mux_asset_create_failed",
  "message": "Mux rejected the upload credentials.",
  "mux": {
    "token_id_present": true,
    "token_secret_present": true,
    "asset_create_attempt": true,
    "asset_create_status": "failed",
    "asset_create_status_code": 401,
    "asset_create_error_type": "unauthorized"
  }
}
```

## Remaining Production Check

After Railway deploys this commit:

1. Upload a fresh MP4/MOV/WebM Reel video.
2. Confirm Mux Dashboard -> Assets shows the new asset.
3. Confirm Railway logs include `PULSE_MUX_ASSET_CREATED`.
4. Confirm `chat_media_uploads` stores `mux_asset_id`, `mux_playback_id`, and `mux_status`.
5. Confirm `/api/pulse/reels/create` creates the Reel immediately with processing state if Mux is not ready yet.

