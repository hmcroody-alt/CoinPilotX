# Pulse Status Upload Failure

Report ID: `pulse_status_upload_failure`

## Endpoint

- Media upload: `POST /api/pulse/media/upload`
- Status create: `POST /api/pulse/status`
- Frontend page: `/pulse/status`

## Request Payload

Media is uploaded first as `multipart/form-data`:

- `file`: selected image/video file
- `context_type`: `pulse_status`
- `context_id`: `dedicated-status-page`

After upload succeeds, Status creation is sent as JSON:

- `status_type`: `text`, `photo`, or `video`
- `body`: optional text
- `media_ids`: uploaded media ids
- `visibility`
- `duration_hours`
- `status_style`
- `ai_context`

## Expected Response Schema

Upload responses must be readable JSON:

```json
{
  "ok": true,
  "success": true,
  "status_id": "",
  "media_url": "...",
  "message": "Media uploaded and verified.",
  "media": {
    "id": 123,
    "media_type": "video",
    "media_url": "...",
    "valid_url": "..."
  }
}
```

Status create responses must also be JSON:

```json
{
  "ok": true,
  "success": true,
  "status_id": 123,
  "status": {},
  "trace_id": "..."
}
```

## Actual Failure Pattern

The frontend message `Upload returned an unreadable response.` is emitted by `static/js/pulse_upload_manager.js` when the upload response cannot be parsed as JSON. The upload manager now records safe diagnostics for:

- HTTP status
- response headers
- content type
- raw response body preview

No file contents or secrets are logged.

## Root Cause

The current concrete upload blocker was `.mov` handling. `services/upload_progress_service.py` rejected MOV/QuickTime uploads when `ffmpeg` was unavailable unless `MEDIA_ALLOW_UNTRANSCODED_MOV` was set. That made Status media posting depend on transcoding infrastructure even though R2/CDN can store and deliver the file.

Additional hardening already present:

- `RequestEntityTooLarge` returns API JSON instead of an HTML 413 page.
- `/api/pulse/media/upload` logs route hit, upload start, completion, and safe trace ids.
- Upload responses include the frontend-friendly `success`, `media_url`, and `message` fields.

## Fix

- MOV uploads are now accepted into the normal media storage path.
- If `ffmpeg` is unavailable, the upload is stored with a safe `processing_note` instead of being rejected.
- `/pulse/status` preview detection now uses file extension fallback, so mobile browsers that send blank MIME types still preview `.mov`, `.mp4`, `.webm`, images, and GIFs correctly.
- The Status upload call passes `video/quicktime` as a fallback media type for `.mov`.

## Verification

Local focused audit proof:

- Text-only Status post: PASS
- Image upload for Status: PASS
- MOV upload for Status: PASS
- Photo-only Status post: PASS
- Text + photo Status post: PASS
- Missing media id returns specific JSON `media_not_found`: PASS
- Non-JSON Status create request returns specific JSON `invalid_content_type`: PASS
- `/api/pulse/media/upload` returns `application/json`: PASS

Production status remains pending until the fix is deployed and tested from the live `/pulse/status` browser flow.
