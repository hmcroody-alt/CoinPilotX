# Desktop Reels Redesign Report

Date: 2026-06-12

## Scope

Redesigned the desktop `/pulse/reels` experience into an immersive media layout inspired by TikTok Web, YouTube Shorts, Instagram Reels, Kick Clips, and X video feeds.

## Files Modified

- `bot.py`
- `reports/pulse_reels_desktop_qa_metrics.json`
- `reports/pulse_reels_desktop_after_1920x1080.png`
- `reports/pulse_reels_desktop_after_2560x1440.png`
- `reports/pulse_reels_desktop_after_laptop_1366x768.png`
- `reports/pulse_reels_desktop_after_ultrawide_3440x1440.png`

## Before vs After

Before reference:
- `reports/pulse_reels_desktop_1440.png`
- Baseline metrics captured before redesign at 1920x1080: shell 1060px wide, video stage 470px wide, one placeholder Up Next thumb, Up Next below the fold.

After screenshots:
- `reports/pulse_reels_desktop_after_1920x1080.png`
- `reports/pulse_reels_desktop_after_2560x1440.png`
- `reports/pulse_reels_desktop_after_laptop_1366x768.png`
- `reports/pulse_reels_desktop_after_ultrawide_3440x1440.png`

## New Layout Architecture

- Desktop shell is full viewport and hides the generic app chrome for Reels.
- Left sidebar is compact navigation with discovery, secondary destinations, and quick actions.
- Center media feed is fixed, scroll-snapped, and dominant.
- Video/reel stage uses 58% to 70% of standard desktop viewport width, with ultrawide allowed to expand further.
- Right column is split into a dedicated active-reel info panel and an Up Next panel.
- Active reel information is mirrored into `#reelsInfoPanel`, outside the scrolling feed, so comments and metadata remain connected without overlay clipping.

## UX Improvements

- Removed the old cramped card layout on desktop.
- Moved Like, Comment, Share, Save, and Repost onto the video.
- Added action labels, pop states, floating emoji burst behavior, optimistic reaction counts, and rollback on failure.
- Added a visible quality pill for adaptive HD/Mux behavior.
- Right panel now shows creator verification, AI trust score, safety status, compact stats, live comments, and comment composer.
- Comment composer posts optimistically in the visible right panel.

## Recommendation Improvements

- Replaced placeholder Up Next cards with media-backed recommendations only.
- Up Next items include thumbnails/videos, creator names, views, duration, likes, and comments.
- Autoplay next remains available.
- Infinite recommendation loading still uses the existing paged Reels feed endpoint.

## Performance Improvements

- Keeps vertical scroll-snap and active-only playback behavior.
- Preloads next reel metadata.
- Removes offscreen placeholder recommendation rendering.
- Keeps mobile-specific Reels behavior scoped under the existing mobile media queries.

## Browser QA

| Viewport | Stage width share | Info panel | Up Next | Actions | Placeholder thumbs |
| --- | ---: | --- | --- | ---: | ---: |
| 1920x1080 | 0.650 | 380px right column | 10 real items | 5 | 0 |
| 2560x1440 | 0.700 | 400px right column | 10 real items | 5 | 0 |
| 1366x768 | 0.583 | 318px right column | 10 real items | 5 | 0 |
| 3440x1440 | 0.759 | 400px right column | 10 real items | 5 | 0 |

Interaction QA:
- Like reaction count changed from 1 to 2.
- Comment count changed from 1 to 2.
- Optimistic comment appeared in the visible right panel.
- Up Next kept 10 real content items and 0 placeholders.

## Validation

Passed:
- `.venv/bin/python -m py_compile bot.py`
- `git diff --check`
- `.venv/bin/python scripts/reels_experience_audit.py`
- `.venv/bin/python scripts/reels_mobile_audit.py`
- `.venv/bin/python scripts/reels_media_load_audit.py`
- `.venv/bin/python scripts/reels_pipeline_audit.py`

Notes:
- Root-level npm validation is not applicable in this repo; there is no root web `package.json`.
- Audit-created media files under `static/uploads/pulse_media/2026/06/13/` were left unstaged.
