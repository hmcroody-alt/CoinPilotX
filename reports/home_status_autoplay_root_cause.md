# Homepage Status Autoplay Root Cause

## Root Cause
Homepage Status previews were able to look like static cards even when the underlying status was video. The viewer path also needed to consistently use the shared Status renderer so homepage and `/pulse/status` behaved the same.

## Fix
- Homepage status cards keep lightweight previews.
- Clicking a homepage status opens the shared Status viewer.
- Video statuses use Mux HLS when a playback ID exists.
- Viewer video playback starts muted with `playsinline` and native-HLS compatible markup.
- Text and image statuses remain full-frame story content.

## Guardrail
`scripts/home_status_autoplay_audit.py` and `scripts/status_shared_viewer_playback_audit.py` verify the shared viewer and autoplay-safe markup.
