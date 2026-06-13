# Mobile Reels Redesign Report

## Summary

Mobile Reels was tightened for fullscreen mobile presentation, route-specific bottom navigation, real media gating, and no-fake-content behavior. The local QA database currently has Reel rows, but every candidate local video file is either missing or a tiny 20-28 byte audit stub, and the Mux/CDN audit URLs return placeholder or 404 responses. The feed now rejects those records instead of rendering broken videos.

## Root Causes Found

- The mobile Reels route hid the global mobile bottom navigation, so the required Home/Reels/Create/Messages/Profile nav was not visible.
- The shell bottom nav did not include a Reels tab by default; it used Music in that slot.
- The feed selected image-first or media-less legacy records before playable video records.
- `pulse_reels.video_url` and attached media rows contained stale local `/static/uploads/...` paths whose files no longer exist.
- Some remaining local media files exist but are only 20-28 byte audit stubs, so browser video metadata never loads.
- Known audit URLs such as `mux_playback_audit.m3u8` and `cdn.coinpilotx.app/audit/search-reel.mp4` were treated as playable candidates.

## Files Changed

- `bot.py`
- `scripts/pulse_mobile_reels_experience_audit.py`
- `reports/mobile_reels_redesign_report.md`
- `reports/pulse_reels_mobile_after_iphone_390x844.png`
- `reports/pulse_reels_mobile_after_android_412x915.png`

## Layout Changes

- Mobile Reels keeps a fullscreen `100dvh` shell and media stage.
- The route now restores the five-item mobile bottom nav on Reels.
- The Music nav slot is converted to Reels only on `/pulse/reels`.
- Messages keeps a red badge indicator.
- The top rail now includes a literal `Reels` label followed by For You, Following, and the discovery lanes.
- Caption, sound, upload, and empty-state controls are offset above the bottom nav.

## Video/Data Changes

- Reels media is sorted so video media is selected before images.
- Feed payloads now require a playable video source before returning a Reel.
- Local `/static` video URLs must exist and be at least 1 KB.
- Known audit placeholder URLs are rejected.
- Supplemental feed loading now includes Reel rows backed by post-attached video media, not only rows with `pulse_reels.video_url`.
- If no valid backend videos exist, the page shows the create/upload empty state instead of fake or broken content.

## QA Browser Results

- iPhone viewport `390x844`: PASS for fullscreen shell, no horizontal overflow, visible Reels rail, visible empty state, upload/create CTAs, five-item bottom nav, Reels active, Messages red badge, and zero console errors.
- Android viewport `412x915`: PASS for fullscreen shell, no horizontal overflow, visible Reels rail, visible empty state, upload/create CTAs, five-item bottom nav, Reels active, Messages red badge, and zero console errors.
- Real backend content: PASS with caveat. The app now refuses to show fake/broken local data. Current local DB has no decodable Reel video, so the verified behavior is the safe empty state.
- Reaction/action stack: not exercised in final browser pass because no valid local Reel video remains after filtering broken data. Existing action markup remains present for valid Reels.

## Screenshots

- Before reference: `reports/pulse_reels_mobile_390.png`
- After iPhone: `reports/pulse_reels_mobile_after_iphone_390x844.png`
- After Android: `reports/pulse_reels_mobile_after_android_412x915.png`

## Validation

- `python -m py_compile bot.py`: PASS
- `scripts/pulse_mobile_reels_experience_audit.py`: PASS (`valid_reels_returned=0`, broken/stub media filtered)
- Browser QA: PASS for corrected no-fake-content empty state

## Known Remaining Issue

The local development database needs real decodable Reel uploads to fully exercise playback, reaction, comment, follow, save, share, and swipe behavior in Browser QA. The code path is prepared for valid backend videos, but this checkout's local media rows are stale or stubbed.
