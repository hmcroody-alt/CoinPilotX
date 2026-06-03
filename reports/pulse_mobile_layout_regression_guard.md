# Pulse Mobile Layout Regression Guard

## Guardrails Added
- Shared `PulseVideo` export is required by audits.
- Reels mobile guard stylesheet is loaded after inline shell CSS with `mobile-lock-20260603`.
- Shared media stylesheet uses `mobile-mux-20260603` so fallback visibility fixes are not hidden by stale cache.
- Mobile Reels hides desktop-only panels:
  - `.reel-details-panel`
  - `.reels-desktop-intel`
  - `.reel-comments-preview`
  - `.reel-inline-comment`
- Mobile Reels locks the card and media stage to full viewport height.
- Mobile Feed video cards are capped to compact heights and keep reaction/comment UI from dominating.
- Feed comments for video posts stay collapsed by default.

## Audit Coverage
- `scripts/pulse_mobile_layout_regression_audit.py`
- `scripts/pulse_mobile_hls_support_audit.py`
- `scripts/pulse_mobile_video_tag_audit.py`
- `scripts/pulse_mobile_video_playback_audit.py`
- `scripts/pulse_reels_mobile_playback_audit.py`
- `scripts/pulse_feed_mobile_playback_audit.py`
- `scripts/pulse_status_mobile_playback_audit.py`
- `scripts/pulse_status_viewer_audit.py`
- `scripts/pulse_unified_video_player_audit.py`
- `scripts/mux_migration_audit.py`

## Fail Conditions Now Covered
- Mobile source can not prefer a raw `.mov` or raw upload when `mux_playback_id` exists.
- Mobile HLS must use `application/vnd.apple.mpegurl`.
- Video tags must include `playsinline` and `webkit-playsinline`.
- Mobile Reels must not expose the desktop details panel.
- Mobile Reels must not expose comments preview or inline comment input by default.
- Generic media fallback must remain hidden until a true broken state.

## Visual Evidence
- `reports/pulse_reels_mobile_390.png`
- `reports/pulse_feed_mobile_390.png`
- `reports/pulse_status_mobile_390.png`
- `reports/pulse_reels_desktop_1440.png`
- `reports/pulse_feed_desktop_1440.png`
