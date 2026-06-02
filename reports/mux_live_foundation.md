# Mux Live Streaming Foundation

Date: 2026-06-02

## Scope

Phase 1 adds the backend and UI foundation for using Mux as the primary Pulse Live video infrastructure while preserving existing Pulse Live routes and data.

## Configuration

Required production variables:

- `MUX_TOKEN_ID`
- `MUX_TOKEN_SECRET`
- `MUX_WEBHOOK_SECRET`

Secrets remain backend-only. The app exposes only safe readiness booleans and public playback URLs.

## Backend Service Methods

Implemented in `services/mux_live_service.py`:

- `create_mux_live_stream()`
- `get_mux_live_stream()`
- `disable_mux_live_stream()`
- `create_mux_asset_from_live_recording()`
- `verify_mux_webhook_signature()`

The service uses Mux Video API endpoints server-side and never sends token secrets to the frontend.

## Database Additions

Pulse Live session and stream records now support:

- `mux_live_stream_id`
- `mux_stream_key`
- `mux_playback_id`
- `mux_live_status`
- `mux_recording_asset_id`
- `mux_recording_playback_id`

These fields are additive and do not destructively modify existing Pulse Live data.

## API Routes

Added:

- `POST /api/pulse/live/mux/create`
- `GET /api/pulse/live/mux/<id>`
- `POST /api/pulse/live/mux/disable`
- `POST /api/pulse/live/mux/webhook`

Existing route updated:

- `POST /api/pulse/live/start`

When Mux is configured, Pulse Live start creates a Mux live stream and uses Mux HLS playback. If Mux is unavailable, the existing native Pulse Live setup remains as fallback.

## Playback Behavior

Hosts receive:

- RTMP ingest URL
- Host stream key
- Studio URL

Viewers receive only the Viewer playback URL:

`https://stream.mux.com/{PLAYBACK_ID}.m3u8`

The playback manifest now prefers Mux playback IDs before native Pulse HLS URLs.

## Webhooks

Webhook verification uses `MUX_WEBHOOK_SECRET` and validates `X-Mux-Signature`.

Handled events:

- `video.live_stream.created`
- `video.live_stream.connected`
- `video.live_stream.disconnected`
- `video.asset.ready`
- `video.asset.errored`

Webhook updates are stored in Pulse Live session/stream records and logged through `pulse_live_events`.

## Replays

Mux recording fields are stored on Live sessions:

- `mux_recording_asset_id`
- `mux_recording_playback_id`

Replay helpers prefer Mux recording playback when available.

## Frontend Foundation

Pulse Live setup now includes a Mux Live foundation placeholder.

Pulse Live Studio shows host-only setup details:

- stream status
- viewer playback URL
- host RTMP ingest URL
- host stream key

The stream key is not exposed to viewer APIs or public Live pages.

## Remaining Production Verification

After deployment:

1. Confirm `MUX_TOKEN_ID`, `MUX_TOKEN_SECRET`, and `MUX_WEBHOOK_SECRET` are present.
2. Start a Pulse Live session as the owner/eligible host.
3. Confirm Mux creates a live stream.
4. Confirm `mux_live_stream_id`, `mux_stream_key`, and `mux_playback_id` persist.
5. Confirm viewer playback URL starts with `https://stream.mux.com/`.
6. Connect OBS/RTMP to the Mux ingest URL and stream key.
7. Confirm Mux webhook events update stream status.
8. End stream and confirm replay asset/playback IDs persist when recording is ready.
