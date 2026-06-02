# Production Media Playback Root Cause

Date: 2026-06-02

## Scope

Investigated production playback failure for the newest failed Reel and newest
failed feed video. Posting itself was not modified.

## Browser Evidence

Production pages tested:

- `https://coinpilotx.app/pulse/reels?tab=for_you`
- `https://coinpilotx.app/pulse`

Observed:

- Feed video posts are created and visible.
- Reels are created and visible.
- Video players render.
- The rendered media shell shows `Media could not load. Tap to retry.`

## Newest Failed Reel

Rendered item:

- Reel id: `4`
- Post id: `882`
- Media id: `92`

Rendered media URL:

```text
https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-cf16a1766c74cfd6.mov
```

Direct URL probe:

```text
HTTP status: 403
Server: cloudflare
Content-Type: text/html; charset=UTF-8
cf-mitigated: challenge
Body: Cloudflare challenge HTML
```

Production API/database-derived fields from `/api/pulse/reels/feed`:

```text
media_url: https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-cf16a1766c74cfd6.mov
valid_url: https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-cf16a1766c74cfd6.mov
cdn_url: empty in payload
playback_url: direct CDN .mov before fix
mux_playback_id: empty
asset_status: not present in payload
processing_status: ready
transcoding_status: ready
verification_status: not present in payload
storage_provider: r2
storage_key: pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-cf16a1766c74cfd6.mov
mime_type: video/quicktime
media_type: video
is_available: true
```

## Newest Failed Feed Video

Rendered item:

- Post id: `883`
- Media id: `93`

Rendered media URL:

```text
https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-a9ebf372283696ad.mov
```

Direct URL probe:

```text
HTTP status: 403
Server: cloudflare
Content-Type: text/html; charset=UTF-8
cf-mitigated: challenge
Body: Cloudflare challenge HTML
```

Production API/database-derived fields:

```text
media_url: https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-a9ebf372283696ad.mov
valid_url: https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-a9ebf372283696ad.mov
cdn_url: empty in payload
playback_url: direct CDN .mov before fix
mux_playback_id: empty
asset_status: not present in payload
processing_status: ready
verification_status: not present in payload
storage_provider: r2
storage_key: pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-a9ebf372283696ad.mov
mime_type: video/quicktime
media_type: video
is_available: true
```

## Control Probes

Image objects on the same CDN path succeeded:

```text
https://cdn.coinpilotx.app/pulse_media/2026/06/02/Ads-4ed58ca27dad1d08.png
HTTP status: 200
Content-Type: image/png
```

```text
https://cdn.coinpilotx.app/pulse_media/2026/06/02/IMG_3371-49f4d3658821ce08.jpeg
HTTP status: 200
Content-Type: image/jpeg
```

This rules out a blanket CDN outage and points specifically to video delivery.

## Storage / Processing Findings

- R2 is the active storage provider.
- Media worker reports `ffmpeg present: True`.
- Upload/create endpoints returned `200` in production logs:
  - `POST /api/pulse/media/upload`
  - `POST /api/pulse/reels/create`
  - `POST /api/pulse/posts`
- New video records are marked `ready`/available in the production payload.
- No Mux playback id is present for the failed videos.
- The app therefore rendered the raw R2 CDN `.mov` URL as the playback source.

Production DB could not be queried directly from local Railway variables because
`DATABASE_URL` uses the private Railway hostname `postgres.railway.internal`.
Railway SSH was unavailable because no local SSH key was registered. The
database field evidence above is from the authenticated production JSON payload
that is generated from the DB-backed feed/reels services.

## Root Cause

Classification: **Private/blocked media delivery path**

Exact failure:

- Raw `.mov` playback URLs under `https://cdn.coinpilotx.app/pulse_media/...`
  are being challenged by Cloudflare.
- Browser video elements receive HTML challenge content instead of a video file.
- The player cannot load the media and shows `Media could not load. Tap to retry.`

Why posting still works:

- Uploads reach Flask/Railway.
- Objects are stored in R2.
- Posts/Reels are created.
- The failure happens later when the browser tries to play the raw CDN `.mov`
  URL.

Contributing issue:

- `mux_playback_id` is empty, so there is no Mux HLS fallback.
- The current backend marked R2 videos `ready` after storage verification even
  when the public CDN playback URL was not browser-deliverable.

## Exact Code Needing Correction

- `services/media_service.py`
  - `resolve_media()` selected raw CDN `.mov` as `playback_url`.
- `bot.py`
  - No first-party media stream endpoint existed for R2 video delivery.
- `services/media_storage.py`
  - Existing R2 upload support did not expose a safe object read helper for
    playback streaming.
- `services/pulse_feed_engine.py`
  - The public feed/Reels media payload dropped `playback_url`, `cdn_url`, and
    Mux fields from the resolved media object, so renderers fell back to raw
    CDN `.mov` URLs.

## Minimal Fix Applied

1. Added `services.media_storage.get_object()` and `object_client()` to fetch R2
   objects server-side without exposing credentials.
2. Added:

```text
GET /api/pulse/media/<media_id>/stream
```

3. The stream endpoint:

- Loads the approved media row by id.
- Allows video/audio only.
- Reads the R2 object by stored object key.
- Returns the correct content type.
- Supports byte range requests with `206 Partial Content`.
- Does not expose R2 credentials.

4. Updated `services.media_service.resolve_media()` so R2/S3 video and audio
   without Mux playback use:

```text
/api/pulse/media/<id>/stream
```

as `playback_url`, while keeping the canonical CDN URL available for diagnostics.

5. Updated `services.pulse_feed_engine._canonical_media_payload()` to preserve
   resolved `playback_url`, `cdn_url`, `mux_playback_id`, `mux_hls_url`, and
   `mux_thumbnail_url` in feed/Reels payloads.

## Expected Post-Fix Behavior

- Feed/Reels still render the same UI.
- Existing R2 video records now resolve to a first-party playback URL.
- Cloudflare no longer serves challenge HTML to the video element for these
  assets because playback is delivered through the app route.
- Mux remains preferred when `mux_playback_id` exists.

## Validation

Local validation passed:

- Python compile
- `scripts/pulse_reels_media_audit.py`
- `scripts/pulse_media_upload_contract_audit.py`
- `git diff --check`

New audit coverage:

- R2 video media resolves to `/api/pulse/media/<id>/stream`.
- CDN URL remains available for diagnostics.
- Pulse media stream endpoint exists.
