# Pulse Video Layout Inventory

Date: 2026-06-06

## Surfaces Covered

- Pulse Feed videos: shared `.post .pulse-media-wrap.media-kind-video`
- Reposted Reel cards: `.pulse-media-surface-reels` and `.reel-card video`
- Original Reel cards: `.pulse-media-surface-reels` and Reels stylesheet
- Pulse Videos page: `.video-thumb .pulse-media-wrap.media-kind-video`
- Video detail pages: `.video-detail-player.pulse-media-wrap.media-kind-video`
- Profile video posts: `.profile-post .pulse-media-wrap.media-kind-video`
- Saved video posts: `.saved-post .pulse-media-wrap.media-kind-video`
- Status viewer: `.pulse-status-story-media .pulse-media-wrap`
- Live replay cards: `.live-replay-card .pulse-media-wrap.media-kind-video`
- Creator previews: `.creator-studio-preview .pulse-media-wrap.media-kind-video`

## Restrictive Rules Reworked

- Final CSS override removes effective 480px/520px video caps.
- Feed media grids are forced to full available width.
- Portrait video wrappers are full-width with max-height rather than narrow centered frames.
- Status and Reels use full-stage cover behavior.
- Detail players are full-width and viewport-scaled.

## Residual Risk

Older inline CSS still exists in `bot.py` for some generated pages, but the shared stylesheet is loaded after the feed CSS on main Pulse surfaces and includes `!important` guardrails to win the cascade.
