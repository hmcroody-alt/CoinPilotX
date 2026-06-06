# Pulse All Video Full Width Fix

Date: 2026-06-06

## Outcome

Added a final shared CSS contract in `static/css/pulse_cinematic_media.css` so Pulse videos fill the available card or viewer width across feed, profile, saved posts, videos page, detail pages, Reels, Status, live replay cards, and creator previews.

## Layout Rules Applied

- Feed/profile/saved videos fill the post media grid width.
- Portrait videos use full card width with controlled viewport-height limits.
- Landscape, square, and ultrawide videos retain aspect ratio at full width.
- Reels and Status remain immersive with `object-fit: cover`.
- Video detail players remain full-width and large.
- Legacy caps such as 480px and 520px are overridden by the final contract.
- Mobile feed uses controlled edge bleed without horizontal scrolling.

## Validation Targets

- `scripts/pulse_all_video_full_width_audit.py`
- `scripts/feed_video_full_width_audit.py`
- `scripts/reels_video_layout_audit.py`
- `scripts/status_video_layout_audit.py`
- Reels, Feed, Status, Videos audits
- Mobile and desktop browser QA
- `git diff --check`
