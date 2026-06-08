# PulseSoc Full Stack Performance Report

## Root Causes

The largest performance pressure was not one single slow route. It was repeated small work during scrolling:

- Media cards carried expensive visual layers and canvas sampling.
- Autoplay could begin while scroll was still active.
- Feed loaded comments too eagerly.
- Notification polling refreshed during active scroll.
- Live diagnostics used a short background interval.

## Web Fixes

- Reduced feed API page size from implicit default 20 to 12 for the live feed page.
- Deferred comment warmup with `requestIdleCallback` and reduced it from 8 posts to 2.
- Made feed live polling scroll-aware.
- Reduced videos page initial payload to 12 records.
- Disabled heavy media layers on mobile CSS.

## API Fixes

- Added `scripts/api_latency_audit.py`.
- Current local API metrics:
  - `/api/pulse/feed?limit=8`: 376ms, 13.7KB, 23 DB queries.
  - `/api/pulse/feed?tab=trending&limit=8`: 51ms, 12.6KB, 9 DB queries.
  - `/api/pulse/videos?limit=8`: 41ms, 6.3KB, 27 DB queries.
  - `/api/pulse/notifications/unread-count`: 16ms, 39B, 8 DB queries.
  - `/api/pulse/notifications?limit=8`: 23ms, 1.9KB, 8 DB queries.
  - `/api/pulse/communications/conversations?limit=12`: 27ms, 15.3KB, 244 DB queries.
  - `/api/pulse/profile/me`: 23ms, 615B, 9 DB queries.

## Database Fixes

Added safe indexes:

- `idx_pulse_posts_mobile_feed`
- `idx_pulse_posts_feed_author`
- `idx_pulse_reactions_user_post`
- `idx_pulse_notifications_user_created`

`scripts/database_query_audit.py` verifies the hot PulseSoc feed, videos, and notification plans.

## iOS Fixes

- WebView cache remains enabled.
- iOS scroll/deceleration configuration is explicit.
- Web media hydration now delays work during active scroll.
- Offscreen videos pause and keep only metadata preloaded.

## Android Fixes

- Android WebView hardware composition is enabled.
- The same website/media optimizations apply to Android because the app loads `https://pulsesoc.com`.

## Validation Metrics

- Site performance audit: pass, 0 failures.
- `/pulse`: 29ms, 9 DB queries.
- `/pulse/reels`: 28ms, 9 DB queries.
- `/pulse/messages`: 26ms, 8 DB queries.
- `/pulse/premium`: 185ms, 10 DB queries.
- Static asset budgets: pass.
- Polling audit: pass after moving live diagnostics to 15 seconds and media processing checks to 18 seconds.
- Mobile performance audit: pass.

## Remaining Risks

- Real-device FPS is still pending the rebuilt iOS/Android install.
- Communications V2 should receive a later query fan-out reduction pass.
- Production latency can differ from local SQLite results, so Railway traces should be reviewed after deploy.
