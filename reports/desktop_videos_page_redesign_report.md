# PulseSoc Desktop Videos Page Redesign Report

Date: 2026-06-12

## Scope

Redesigned the desktop `/pulse/videos` experience into a wider, content-rich video hub while preserving the existing mobile Videos behavior and existing video management endpoints.

## Root Causes Found

- The desktop Videos page was constrained by the shared PulseSoc shell width, leaving the content area at roughly 846px on a 1920px desktop viewport.
- The featured hero, trending creators, and trending videos panels existed structurally but were not populated by the frontend render path.
- The safe-content API filter referenced `pulse_videos.moderation_status`, but the `pulse_videos` schema/migration did not guarantee that column existed.
- Video cards still carried placeholder fallback strings in the listing renderer.
- Creator follow buttons could render on owner videos, creating incorrect optimistic self-follow behavior.

## Files Changed

- `/Users/hmcherie/Desktop/CoinPilotX/bot.py`
- `/Users/hmcherie/Desktop/CoinPilotX/scripts/pulse_desktop_videos_page_audit.py`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/desktop_videos_page_redesign_report.md`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_videos_desktop_after_1920x1080.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_videos_desktop_after_1366x768.png`

## New Desktop Layout Architecture

- Desktop-only shell override expands `/pulse/videos` to `min(100% - 32px, 1880px)`.
- Page grid uses three desktop columns:
  - Left discovery sidebar: For You, Following, Trending, New Creators, AI Picks, Local, Explore, Quick Actions, PulseSoc AI card.
  - Main content: title, subtitle, search, category chips, sorting, grid/list toggle, real featured hero, and New & Hot grid.
  - Right sidebar: filters, trending creators, and trending videos.
- Responsive desktop breakpoints:
  - 1920px and ultrawide: full 3-column layout with 4-card grid.
  - Laptop widths: 3-card grid with sidebars visible.
  - Narrow desktop fallback keeps layout from overflowing.

## Data Sources Used

- `/api/pulse/videos`
- `pulse_videos`
- `users`
- `pulse_follows`
- Existing `pulse_video_payload()` fields and permalink generation.

## Featured Video Logic

- Uses the first ready video from the real API result.
- Falls back to the first real row only if no ready row is available.
- Shows real title, thumbnail/video, duration, creator, follower count, view count, comments, likes, and shares.
- Does not render fake placeholder video cards.

## Video Grid Logic

- Uses real API rows only.
- Shows real thumbnail/video media, duration, title, creator, verification state, visibility/source metadata, and view counts.
- Provides grid/list toggle without page reload.
- Removes listing fallback text like `PulseSoc Video` and `Untitled Video`.

## Right Sidebar Logic

- Trending creators are derived from unique real video owners in the current API result.
- Trending videos are ranked from the current API result by view count.
- Filters are wired to API parameters: category, upload date, duration, sort, and safe content.

## Interaction Improvements

- Category chips update instantly and sync the right-side filter.
- Search debounces and filters against the backend.
- Sort dropdown syncs with the right-side sort filter.
- Grid/list mode toggles instantly.
- Follow buttons use optimistic UI and the existing `/api/pulse/follows/toggle` backend endpoint.
- Owner videos show `Your video` instead of a self-follow button.

## QA Browser Results

PASS - Desktop 1920x1080:
- Page width: 1760px.
- Main column: 1134px.
- Grid: 4 columns.
- Hero visible with real video title: `Audio Video Live`.
- 24 real video cards rendered.
- 5 trending creators rendered.
- 5 trending videos rendered.
- No forbidden placeholder strings visible in the Videos listing.
- No horizontal overflow.

PASS - Laptop 1440x900:
- Page width: 1408px.
- Grid: 3 columns.
- Hero, left sidebar, and right sidebar visible.
- 24 real video cards rendered.
- No horizontal overflow.

PASS - Laptop 1366x768:
- Page width: 1334px.
- Grid: 3 columns.
- Hero, left sidebar, and right sidebar visible.
- 24 real video cards rendered.
- No horizontal overflow.

PASS - Ultrawide 2560x1080:
- Page width capped at 1760px.
- Grid: 4 columns.
- Sidebars remain visible.
- No dead horizontal overflow.

PASS - Category filter:
- Education chip selected.
- Right-side category value synced to `education`.
- Result count updated to 12 videos.

PASS - Search:
- Search for `Transport` returned 13 videos.
- First result title: `Transport Audit`.

PASS - Grid/List toggle:
- List mode applied `videos-grid list`.
- Grid columns collapsed to one column.

PASS - Sorting:
- Sort changed to `most_viewed`.
- Right-side sort filter synced to `most_viewed`.
- 24 videos remained available.

PASS - Follow creator:
- Follow candidate used another creator ID.
- Button changed from `Follow` to `Following`.
- `aria-pressed` changed from `false` to `true`.

PASS - Empty/failure safety:
- Empty state is clean and provides upload/clear-filter actions.
- API 500 root cause was fixed with an additive schema migration.

## Validation Commands

PASS:

```bash
.venv/bin/python -m py_compile bot.py scripts/pulse_desktop_videos_page_audit.py
.venv/bin/python scripts/pulse_desktop_videos_page_audit.py
```

## Screenshot Evidence

- After 1920x1080: `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_videos_desktop_after_1920x1080.png`
- After 1366x768: `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_videos_desktop_after_1366x768.png`

## Before vs After

Before:
- Shared shell constrained desktop content to roughly 846px at 1920px viewport.
- Featured hero remained hidden.
- Trending creators and trending videos were empty.
- Listing source still contained placeholder fallback strings.
- Safe-content API could fail due to a missing `moderation_status` column.

After:
- Desktop layout uses the available viewport with a 1760px premium hub.
- Featured hero, grid, trending creators, and trending videos all use real API content.
- No placeholder listing strings are rendered.
- API schema is additive and safe-filter capable.
- Follow actions are scoped to non-owner creators.

## Known Remaining Issues

- This milestone is desktop Videos only. Mobile Videos redesign, mobile Reels redesign, and predictive preloading remain separate requested milestones.
