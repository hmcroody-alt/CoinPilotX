# PulseSoc iOS Scroll Performance Report

## Root Cause

The TestFlight build now wraps the live PulseSoc website in a native WebView. The janky scroll was therefore coming mostly from the web surface inside the WebView, not from native React Native `ScrollView` screens.

Primary causes found:

- Media cards rendered several decorative layers per item.
- The media renderer sampled image/video pixels for ambient colors on mobile.
- Feed images upgraded from thumbnails to full sources during scroll.
- In-view autoplay could start work while the user was actively scrolling.
- Feed and notification refresh work could run during scroll.

## Screens Optimized

- Feed: lighter media hydration, smaller initial API page size, comment warmup reduced from 8 posts to 2 posts and deferred to idle time.
- Videos: initial library load capped to 12 videos.
- Reels and video media: autoplay is scheduled through `requestAnimationFrame`, paused offscreen, and delayed briefly while active scrolling is detected.
- Messages/Notifications/Profile/Premium: native WebView keeps cache enabled and notification polling no longer refreshes during active scrolling.

## List Rendering Strategy

PulseSoc mobile is now a WebView shell, so native `FlatList`/`FlashList` is not the correct layer. The equivalent strategy is:

- Live website remains the source of truth.
- Website lists keep server pagination.
- Media hydration is lazy and viewport-aware.
- Offscreen video work is paused.
- Decorative layers are disabled on small screens/WebView.

## Before/After Notes

Before:

- Autoplay and media hydration could run immediately as items crossed the viewport.
- Background notification polling ran every 12 seconds.
- Feed loaded comments for the first 8 posts after each load.

After:

- Media hydration root margin on mobile reduced to 220px.
- Background notification polling increased to 30 seconds and becomes scroll-aware.
- Processing media poll increased to 18 seconds.
- Live diagnostics poll increased to 15 seconds.
- Feed local route audit: `/pulse` 29ms, 9 DB queries.
- Feed API audit: `/api/pulse/feed?limit=8` 376ms, 23 DB queries, 13.7KB.

## Remaining Risks

- iOS build `15` was uploaded to App Store Connect/TestFlight processing. Real iPhone FPS must still be judged after Apple finishes processing and the build is installed.
- Communications API still performs 244 DB queries in the local audit; latency is low locally, but it remains a future backend optimization target.
- WebView smoothness can vary by device, battery state, and network media cache warmth.
