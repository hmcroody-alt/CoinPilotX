# PulseSoc Video Watch Page Redesign

Date: 2026-06-14

## Scope

Redesigned the PulseSoc video detail/watch page for mobile and desktop after the video-routing fix.

## Root Causes Addressed

- The detail page still behaved like a feed-card/detail hybrid instead of a dedicated video watch surface.
- Internal source labels such as `feed_video` were visible to users.
- Comments and stat boxes occupied too much visual weight.
- Desktop did not provide a real watch-page frame with left navigation and a persistent Up Next rail.
- Related/replay rows could include invalid live-derived playback records.
- A shared cinematic feed-media stylesheet and hydrator were forcing `.video-detail-player` into a tall feed-video shape.
- The global media stylesheet was suppressing native video controls for the video detail page.
- Some records stored `.webm` files in `thumbnail_url`, which rendered as broken image thumbnails in Up Next.

## Changes

- Added a PulseSoc-branded mobile watch header with back, search, and menu actions.
- Added a desktop watch shell with:
  - top app bar
  - left navigation rail
  - main cinematic video column
  - sticky Up Next rail
- Reordered the watch card so the video player is first and dominant.
- Replaced leaked source labels with human-facing labels such as `PulseSoc Video`.
- Converted giant stat boxes into a compact engagement summary.
- Rebuilt the action bar with glass/teal styling and Like, Comment, Repost, Share, Save.
- Added compact Top Comment preview and a slim comment composer.
- Added lightweight Pulse Insights card.
- Added mini-player scaffold for scroll-away behavior.
- Filtered related videos to avoid live/replay/stale playback rows.
- Isolated the watch page player from shared feed media hydration with a dedicated `pulse-video-watch-player` class.
- Added PulseSoc watch controls overlay: quality pill, play orb, compact progress bar, settings/caption/fullscreen affordances.
- Added safe poster filtering so video URLs are not used as image posters or related thumbnails.
- Added fallback Up Next thumbnail treatment when a real image thumbnail is unavailable.

## Files Changed

- `bot.py`
- `static/css/pulse_cinematic_media.css`
- `reports/pulse-video-watch-redesign/mobile-watch-after.png`
- `reports/pulse-video-watch-redesign/mobile-mini-player-after.png`
- `reports/pulse-video-watch-redesign/mobile-watch-real-video-after.png`
- `reports/pulse-video-watch-redesign/desktop-watch-after.png`
- `reports/pulse-video-watch-redesign/qa-results.json`

## Validation

- `python -m py_compile bot.py`
- `python -m py_compile scripts/pulse_feed_video_routing_audit.py`
- `python scripts/pulse_feed_video_routing_audit.py`
- `git diff --check`
- Browser QA with Playwright:
  - Mobile iPhone viewport
  - Desktop 1440px viewport
  - Related video navigation
  - Mini-player after scroll

## QA Results

- Mobile watch page: PASS
  - Player first and dominant.
  - Real MP4 poster/video test passed with `readyState=4`.
  - No `feed_video` text.
  - No offline fallback.
  - Up Next, actions, comments, and insights present.
  - Mini-player appears after scrolling away from the video.
  - 0 relevant console errors.
  - 0 relevant 4xx/5xx responses.

- Desktop watch page: PASS
  - Desktop app bar visible.
  - Left navigation rail visible.
  - Sticky Up Next visible.
  - Player first and dominant.
  - Real poster appears in player.
  - No `feed_video` text.
  - Related videos are visible immediately in the right rail.
  - Related video click opens `/pulse/videos/231` with a valid player.
  - 0 relevant console errors.
  - 0 relevant 4xx/5xx responses.

## Screenshots

- `reports/pulse-video-watch-redesign/mobile-watch-after.png`
- `reports/pulse-video-watch-redesign/mobile-watch-real-video-after.png`
- `reports/pulse-video-watch-redesign/mobile-mini-player-after.png`
- `reports/pulse-video-watch-redesign/desktop-watch-after.png`

## Known Limitations

- Some older local QA videos have no real image thumbnail and no duration metadata. The page now displays a branded fallback instead of a broken thumbnail and avoids fake counts/durations.
