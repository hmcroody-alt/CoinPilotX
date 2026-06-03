# Pulse Large Video Upload Fix

## Root Cause

Pulse video uploads were still using the standard multipart endpoint:

- `POST /api/pulse/media/upload`

That route sends the entire file through the browser, Cloudflare, Railway, Flask, and then storage/Mux. This is reasonable for photos and smaller clips, but it is the wrong path for long mobile videos. A 51-second mobile recording can already exceed the standard upload threshold, and a 3.5-hour video should not be proxied through the web process.

## Fix

Large videos now use a direct-to-Mux upload lane:

1. Browser asks Pulse for a Mux direct upload URL:
   - `POST /api/pulse/media/mux/direct-upload`
2. Pulse creates a placeholder `chat_media_uploads` row and stores `mux_upload_id`.
3. Browser uploads the video bytes directly to Mux with `PUT`.
4. Browser tells Pulse the upload completed:
   - `POST /api/pulse/media/mux/direct-upload/complete`
5. Pulse returns the normal `media` object so Feed, Reels, and Status can publish immediately.
6. Mux webhooks update media rows when the asset is created, ready, or errored.

## Limits

The default direct-upload application limit is:

- `MEDIA_DIRECT_UPLOAD_MAX_VIDEO_GB=25`

This is meant to support multi-hour uploads without forcing the web app to buffer the file. Final acceptance still depends on Mux account limits, browser/network reliability, and any platform-level upload rules.

## Safety

- Photos, audio, files, and small media keep using the existing upload route.
- Large videos no longer hit the normal request-size guard.
- Mux secrets are not exposed to the browser.
- The browser receives only a signed Mux upload URL.
- Webhook events update media status without exposing secrets.

## Validation

Added:

- `scripts/pulse_mux_direct_upload_audit.py`

The audit verifies direct upload routes, frontend direct upload selection, `mux_upload_id` persistence, Mux webhook handling, and clear large-video error messages.
