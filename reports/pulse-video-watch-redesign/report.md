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

## Files Changed

- `bot.py`
- `reports/pulse-video-watch-redesign/mobile-iphone-watch-redesign.png`
- `reports/pulse-video-watch-redesign/desktop-1440-watch-redesign.png`
- `reports/pulse-video-watch-redesign/qa-results.json`

## Validation

- `python -m py_compile bot.py`
- `git diff --check`
- Browser QA with Playwright:
  - Mobile iPhone viewport
  - Desktop 1440px viewport

## QA Results

- Mobile watch page: PASS
  - Player first and dominant.
  - No `feed_video` text.
  - No offline fallback.
  - Up Next, actions, comments, and insights present.
  - 0 relevant console errors.
  - 0 relevant 4xx/5xx responses.

- Desktop watch page: PASS
  - Desktop app bar visible.
  - Left navigation rail visible.
  - Sticky Up Next visible.
  - Player first and dominant.
  - No `feed_video` text.
  - 0 relevant console errors.
  - 0 relevant 4xx/5xx responses.

## Screenshots

- `reports/pulse-video-watch-redesign/mobile-iphone-watch-redesign.png`
- `reports/pulse-video-watch-redesign/desktop-1440-watch-redesign.png`
