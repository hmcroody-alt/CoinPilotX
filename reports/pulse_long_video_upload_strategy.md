# Pulse Long Video Upload Strategy

Pulse already has the correct first step for three-hour-plus videos: large video files use the direct Mux upload flow instead of passing through Flask/Gunicorn.

- Direct upload threshold: 20 MB by default.
- Configured safety ceiling: `MEDIA_DIRECT_UPLOAD_MAX_VIDEO_GB`, defaulting to 25 GB.
- Browser uploads directly with progress reporting.
- Pulse creates the content record while Mux processing continues asynchronously.
- Ready playback uses `https://stream.mux.com/{playback_id}.m3u8`.

True resumable recovery after a browser/device restart remains the next reliability milestone. Provider/account duration and storage limits must be confirmed before advertising an unlimited duration.
