# PulseSoc Video Routing and Feed Video Card Fix

Date: 2026-06-14

## Root Cause

- Home feed video cards were rendered from post payloads whose generic permalink pointed to `/pulse/post/<post_id>`, while the modern video player route lives at `/pulse/videos/<video_id>`.
- The active Home feed click handler for `data-open-media-lightbox` treated video taps as inline media interactions instead of safe internal video navigation.
- Service workers returned the full "PulseSoc is offline" fallback for any navigation fetch failure, even when the browser was online.
- The Videos hub and related-video surfaces could render stale Live/Mux rows or local file-backed rows whose playback files no longer existed, creating 404 media requests during QA.

## Fix Implemented

- Added backend feed enrichment that attaches validated `video_id` and `video_permalink` values to Home feed video posts and their video media items.
- Added lazy recovery/indexing for older feed video posts that have not yet been represented in `pulse_videos`.
- Updated Home feed rendering so video posts use `/pulse/videos/<video_id>` anchors with full-card click behavior.
- Updated the Home feed card layout so video posts are video-first:
  - media appears before creator metadata
  - video frame dominates the card
  - reactions/views are overlaid directly on the media
  - creator and caption are secondary below the media
- Updated service workers so the offline page is only used when the browser is actually offline; online navigation failures now show a route-specific retry message.
- Updated video detail related-video filtering to exclude Live rows from ordinary related videos.
- Updated the Videos API to exclude Live/Replays from default video discovery and drop ready video rows when their local playback URL is missing.

## Files Changed

- `bot.py`
- `static/js/pulse_home_core.js`
- `static/css/pulse_desktop_feed.css`
- `static/sw.js`
- `static/service-worker.js`
- `scripts/pulse_feed_video_routing_audit.py`
- `reports/pulse-video-routing-fix/`

## Validation

- `python -m py_compile bot.py scripts/pulse_feed_video_routing_audit.py`
- `python scripts/pulse_feed_video_routing_audit.py`
- `node --check static/js/pulse_home_core.js`
- `node --check static/sw.js`
- `node --check static/service-worker.js`
- `git diff --check`

## Browser QA Results

All checks passed.

- Mobile Home feed video card: `/pulse/videos/231`, frame `372x574`, card `374x820`
- Mobile Home feed video click: opened `http://127.0.0.1:5088/pulse/videos/231`, no offline fallback, 0 relevant console errors, 0 relevant 4xx/5xx responses
- Desktop Home feed video card: `/pulse/videos/231`, frame `758x720`, card `760x985`
- Desktop Home feed video click: opened `http://127.0.0.1:5088/pulse/videos/231`, no offline fallback, 0 relevant console errors, 0 relevant 4xx/5xx responses
- Mobile Videos page click: opened `http://127.0.0.1:5088/pulse/videos/233`, no offline fallback, 0 relevant 4xx/5xx responses
- Mobile profile/my-posts video click: opened `http://127.0.0.1:5088/pulse/videos/231`, no offline fallback, 0 relevant 4xx/5xx responses

## Screenshots

- `reports/pulse-video-routing-fix/mobile-iphone-home-video-card.png`
- `reports/pulse-video-routing-fix/mobile-iphone-video-detail.png`
- `reports/pulse-video-routing-fix/desktop-home-video-card.png`
- `reports/pulse-video-routing-fix/desktop-video-detail.png`
- `reports/pulse-video-routing-fix/mobile-videos-page.png`
- `reports/pulse-video-routing-fix/mobile-profile-my-posts-video.png`

## Remaining Notes

- Local QA created temporary verified users and QA video posts in the local SQLite database only.
- Existing unrelated worktree edits in messages/profile areas were not part of this fix and should not be staged with this commit.
