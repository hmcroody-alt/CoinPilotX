# Reels Load Speed Operation

## Summary

PulseSoc Reels now favors perceived instant playback:

- The first feed request uses a smaller page size.
- The first three cards render immediately after the API returns.
- Remaining cards hydrate during idle time.
- The active video is promoted to `preload=auto`.
- The next two videos are warmed silently.
- The previous video stays cached briefly for reverse swipes.
- Far offscreen videos are paused and unloaded with `preload=none`.
- A lightweight poster/skeleton layer prevents a blank black waiting state.

## Backend Changes

- Added `include_preview_comments` to `pulse_reel_payload` and `pulse_reel_feed_payload`.
- Initial `/api/pulse/reels/feed` calls no longer run per-reel comment-preview queries unless `include_comments=1` is explicitly requested.
- Changed the client first-page and load-more request size from `12` to `8`.
- Added Reels-focused database indexes for post feed filtering, reel status/score ordering, reel/post joins, comment-thread lookup, and media context lookup.

## Frontend Changes

- Each reel card now carries `data-reel-preload-priority`:
  - `current` for the first visible reel
  - `nearby` for the next two
  - `lazy` for the rest
- `loadReels()` renders the first 3 cards first, schedules playback, then appends the remaining cards via idle callback.
- `preloadNextReel()` now manages the previous card plus the next two cards.
- `releaseFarReelMedia()` pauses and unloads videos outside the active window.
- Reels timing diagnostics were added:
  - `firstReelApiMs`
  - `videoMetadataMs`
  - `videoCanplayMs`
  - `firstFrameMs`
  - `preloadSuccess`

## Media Behavior

- Posters are warmed for near-window reels.
- Offscreen videos pause automatically.
- Far videos release buffers while preserving their source for later reload.
- Existing PulseSoc sound preference, autoplay fallback, attached audio handling, and retry paths remain intact.

## Files Changed

- `bot.py`
- `migrations/pulsesoc_reels_load_speed_indexes.sql`
- `scripts/reels_load_speed_audit.py`
- `reports/reels_load_speed_operation.md`

## QA Results

- `venv/bin/python -m py_compile bot.py scripts/reels_load_speed_audit.py`: passed.
- `node --check static/js/pulse_media_renderer.js`: passed.
- `venv/bin/python scripts/reels_load_speed_audit.py`: passed.
- `venv/bin/python scripts/reels_media_load_audit.py`: passed.
- `venv/bin/python scripts/reels_pipeline_audit.py`: passed.
- `git diff --check`: passed before staging.
- `curl -fsS http://127.0.0.1:5069/health`: passed.
- `curl -fsS http://127.0.0.1:5069/health/live`: passed.
- `curl -fsS http://127.0.0.1:5069/health/ready`: passed.
- In-app browser smoke against the already-running local server confirmed the Reels shell loads, has no horizontal overflow, and renders media-backed cards. That server had been started before this patch, so it did not prove the new first-chunk markup live in the browser; the new code path is covered by compile and static Reels audits.

## Known Limitations

- Real first-frame time still depends on device decode policy, network speed, video encoding, and CDN/R2 cache state.
- This pass does not transcode existing oversized originals. It prioritizes efficient feed startup and client lifecycle management.
- HLS/adaptive rendition quality should be verified separately for media uploaded before the current pipeline.
