# Pulse Full UX Repair

Date: 2026-06-03

## Summary

This pass repairs high-impact Pulse UX contracts across Status, the homepage Status rail, the mobile composer, feed filters, profile editing, and visible button behavior. The work keeps existing backend media/Mux infrastructure intact and adds guardrail audits for the most fragile surfaces.

## Repairs

- Status media picking now works from the default text mode. Users can tap Add photo or video without first selecting Photo or Video.
- Dedicated Status posts still allow text-only, media-only, and text plus media.
- Recent Status cards and home Status cards now show readable content before opening.
- Video Status cards loop a muted preview teaser before the user opens the story.
- Status creation reloads the Recent Status rail and scrolls the newly created card into view.
- The Global Pulse Feed hero dots now rotate with low-cost CSS animation and respect reduced motion.
- Mobile feed tabs are horizontally scrollable with clear active states.
- Pulse composer controls remain reachable on mobile and publish errors remain explicit.
- Profile editing now exposes avatar, cover, display name, username/handle, bio, website/social links, expertise tags, and privacy controls with clear save feedback.

## Guardrails Added

- `scripts/pulse_button_functionality_audit.py`
- `scripts/pulse_status_upload_viewer_audit.py`
- `scripts/pulse_home_status_layout_audit.py`
- `scripts/pulse_composer_mobile_audit.py`
- `scripts/pulse_profile_edit_audit.py`
- `scripts/pulse_side_panel_layout_audit.py`
- `scripts/pulse_landscape_video_upload_audit.py`
- `scripts/pulse_feed_filter_visibility_audit.py`

## Remaining External Dependency

Fresh production video upload success still depends on Railway environment values and the public R2 Mux source URL being configured correctly. The code continues to prefer `MUX_SOURCE_BASE_URL` or `R2_MUX_SOURCE_BASE_URL` for Mux source ingestion and uses Mux HLS for playback when available.
