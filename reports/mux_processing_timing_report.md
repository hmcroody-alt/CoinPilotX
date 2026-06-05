# Mux Processing Timing Report

## Added Timing Fields
- `upload_complete_at`
- `mux_asset_created_at`
- `mux_ready_at`
- `webhook_received_at`
- `db_ready_update_at`

## UX Changes
- Processing media shows “Preparing video...” instead of a generic failure.
- The shared player checks readiness every 12 seconds.
- After 3 minutes, the message changes to “Processing is taking longer than usual.”
- Owners/admins can trigger a repair/check action.

## Data Sync
Readiness repair updates:
- `chat_media_uploads`
- `pulse_reels`
- `pulse_videos`

## Guardrail
`scripts/mux_processing_timing_audit.py` and `scripts/reel_processing_repair_audit.py` verify timing fields, status endpoint, repair endpoint, and frontend polling cues.
