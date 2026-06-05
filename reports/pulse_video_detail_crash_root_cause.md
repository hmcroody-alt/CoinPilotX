# Pulse Video Detail Crash Root Cause

## Root cause

`/pulse/videos` rendered cards from `pulse_videos`, but each card linked back to the source surface (`/pulse/post/<id>`, `/pulse/live/<id>`, or reels) instead of a stable video detail route. Ready, processing, failed, archived, or partially indexed video records could therefore open a route that did not have the expected video data shape and could fall into the system issue page.

## Fix

- Added stable `/pulse/videos/<video_id>` routing for every video card.
- Normalized `pulse_video_payload().permalink` to the new detail route while keeping `source_url` for source navigation.
- Added clean Pulse-layout 404 handling for missing, private, deleted, or archived videos.
- Added safe trace logging for detail failures and missing records.
- Added ready, preparing, and failed states on the detail page.
- Added owner-only retry, edit, and delete actions.
- Kept Mux HLS as the preferred playback source when `mux_playback_id` exists, with fallback playback when available.

## User-facing result

Clicking any Pulse video card now opens a Pulse video detail page instead of a system issue page. Non-ready videos show a clear status instead of crashing.
