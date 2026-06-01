# Pulse Status Upload Failure

Report ID: `pulse_status_upload_failure`

## Endpoint

- Media upload: `POST /api/pulse/media/upload`
- Status create: `POST /api/pulse/status`

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

Upload responses now include:

```json
{
  "ok": true,
  "success": true,
  "status_id": "",
  "media_url": "...",
  "message": "Media uploaded and verified.",
  "media": {
    "id": 123,
    "media_url": "...",
    "valid_url": "..."
  }
}
```

Status create responses include:

```json
{
  "ok": true,
  "status_id": 123,
  "status": {},
  "trace_id": "..."
}
```

## Actual Failure Pattern

The frontend error `Upload returned an unreadable response.` is produced by `static/js/pulse_upload_manager.js` when the upload response cannot be parsed as JSON.

The most likely mismatches are:

- An HTML login or framework error page returned to an API upload request.
- An oversized upload rejected before the Pulse upload route can format JSON.
- A malformed/empty response during media storage failure.
- A `.mov` validation failure hidden by generic frontend parsing.

## Root Cause

The upload manager previously discarded the HTTP status, content type, headers, and raw response body when parsing failed, so different server responses collapsed into the same unreadable message. The Pulse media route also returned only the legacy `ok/media` envelope and did not expose the `success/media_url/message` schema requested by the Status flow.

## Fix

- Added upload parse diagnostics in `static/js/pulse_upload_manager.js`.
- Added JSON handling for oversized API uploads.
- Added route-hit, upload-start, completion, and failure logging in `/api/pulse/media/upload`.
- Added a compatibility upload response envelope with `success`, `media_url`, and `message`.
- Preserved existing `ok` and `media` fields so feed, camera, Status, and Reels flows keep working.

## Verification Targets

- Text-only Status posts through `/api/pulse/status`.
- Image Status uploads through `/api/pulse/media/upload`, then posts through `/api/pulse/status`.
- Video Status uploads through `/api/pulse/media/upload`, then posts through `/api/pulse/status`.
- `.mov` either uploads when supported or returns a readable JSON error such as MOV conversion guidance.
- Upload parse failures now expose safe diagnostics in the browser console without logging file contents or secrets.
