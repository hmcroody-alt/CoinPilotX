# Content Preloading And Scroll Performance Report

## Scope

Added predictive media preloading across PulseSoc media surfaces that use `PulseMediaRenderer`: Reels, Videos, post media, mixed media feeds, status viewer media, status rail media, and message media.

## Root Causes Found

- The shared renderer only preloaded one next video and only from the active playback path.
- Images, posters, audio, status media, and generic mixed media wraps did not have a shared rolling preloader.
- There was no bounded cache or stale-request cancellation for quick direction changes.
- Renderer script URLs were cache-busted to older builds, so clients could retain the previous behavior.

## Preloading Architecture Added

- Added a central rolling preloader in `static/js/pulse_media_renderer.js`.
- Rolling buffer:
  - previous 1 item
  - current item
  - next 2 items
- Uses `IntersectionObserver` to schedule nearby items.
- Uses `requestIdleCallback` where available to avoid blocking scroll.
- Uses a bounded in-memory cache capped at 72 media keys.
- Cancels stale preload controllers when the user moves to a different window.
- Respects constrained network signals through `navigator.connection.saveData` and 2G effective types.

## Feed And Media Types Covered

- Reels and full-screen video cards through `.reels-immersive` and `.pulse-media-wrap`.
- Videos page cards and featured media through `#videosGrid` and `.videos-grid`.
- Status viewer media through `[data-status-viewer]`.
- Status rail/home videos through `[data-status-home-video]`.
- Feed and mixed media posts through `.feed`, `[data-feed]`, and generic `main` media wrappers.
- Message media through `.messages-list`.

## Cache Strategy

- Current media receives higher priority and can use `preload="auto"`.
- Nearby media receives metadata/auto preload depending on network conditions.
- Posters and thumbnails are warmed with `Image()` before the user reaches the next item.
- Older cache entries are evicted once the bounded cache exceeds 72 entries.

## Backend/API Notes

- `/api/pulse/videos` already returns preload metadata needed by the frontend: media/playback URL, thumbnail/poster when available, duration, permalink, creator info, counts, and content type/source fields.
- The audit confirms these fields are present for the local QA account.

## QA Browser Results

- PASS static JS syntax: `node --check static/js/pulse_media_renderer.js`
- PASS Python compile: `python -m py_compile bot.py scripts/pulse_predictive_media_preload_audit.py`
- PASS audit: `python scripts/pulse_predictive_media_preload_audit.py`
- PASS mobile Videos `390x844`: renderer cache-busted to `predictive-preload-20260612`; 31 media wraps present; rolling preload markers applied to 3 adjacent videos: `current`, `nearby`, `nearby`; no horizontal overflow.
- PASS mobile Reels route load: renderer cache-busted and page stable; local QA database did not expose preloadable `.pulse-media-wrap` reel media for this account.
- PASS mobile Home mixed feed route load: renderer cache-busted and page stable; local QA database did not expose preloadable `.pulse-media-wrap` home media for this account.

## Browser Evidence

- Videos preload screenshot: `reports/pulse_preload_videos_mobile_390x844.png`
- Reels preload route screenshot: `reports/pulse_preload_reels_mobile_390x844.png`
- Home mixed feed preload route screenshot: `reports/pulse_preload_home_mixed_mobile_390x844.png`

## Performance Impact

- The implementation avoids preloading the full feed.
- Only the previous/current/next-two window is actively warmed.
- Stale window requests are aborted/deprioritized.
- Images/posters are warmed separately from playable video metadata to reduce black/blank first frames.
