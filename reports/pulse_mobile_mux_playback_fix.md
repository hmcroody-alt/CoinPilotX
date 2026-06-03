# Pulse Mobile Mux Playback Fix

## Scope
- Focused on Pulse Feed videos, Reels, and Status videos.
- Communications V2 and unrelated UI paths were not changed.
- Credentials were treated as configured; this work did not investigate missing credentials.

## Local Data Path Verification
- Latest local Reel: `reel_id=65`, `post_id=849`, `media_id=1112`.
- Latest local Feed video: `post_id=849`, `media_id=1112`.
- Local media row `1112`:
  - `mux_asset_id`: empty
  - `mux_playback_id`: empty
  - `mux_status`: empty
  - `playback_url`: empty
  - `playback_mime_type`: empty
  - `media_url`: `/static/uploads/pulse_media/2026/06/02/pulse-video-9318a38f2b64dfd4.webm`
  - `processing_status`: `ready`
  - `is_available`: `1`
- No local video rows had non-empty Mux asset/playback fields in this workspace database.

## Mux API Verification
- The local workspace runtime did not expose Mux API tokens, so a local Mux dashboard/API check for these workspace rows could not be completed.
- Production rows with `mux_playback_id` are now forced to render through `https://stream.mux.com/{mux_playback_id}.m3u8`.

## Fixes
- Feed, Reels, Status, and Reel detail now prefer canonical Mux HLS whenever `mux_playback_id` exists.
- `.m3u8` sources are labeled `application/vnd.apple.mpegurl`.
- iOS/Safari uses native HLS through `video.canPlayType(...)`.
- Android/Chrome keeps HLS.js as the non-native fallback.
- Video tags keep `playsinline`, `webkit-playsinline`, `preload="metadata"`, and muted startup.
- Sound unlock persists through `pulseMediaSoundEnabled` and falls back to muted playback if sound autoplay is blocked.
- Shared renderer exports `window.PulseVideo` while preserving `window.PulseMediaRenderer`.
- Premature "Media could not load" overlays are hidden until a wrapper is actually marked broken.

## Browser QA
- Mobile Reels screenshot: `reports/pulse_reels_mobile_390.png`
- Mobile Feed screenshot: `reports/pulse_feed_mobile_390.png`
- Mobile Status screenshot: `reports/pulse_status_mobile_390.png`
- Desktop Reels screenshot: `reports/pulse_reels_desktop_1440.png`
- Desktop Feed screenshot: `reports/pulse_feed_desktop_1440.png`

## Browser Findings
- Mobile Reels at 390x844: Reels stage fills the viewport; desktop details, comments preview, and inline comment input are hidden.
- Mobile Feed: videos and photos render; generic media fallback is not visible.
- Mobile Status: text/photo/video status surfaces remain readable; videos include inline mobile attributes.
- Desktop Reels at 1440x900: desktop detail panel remains visible.
- Desktop Feed at 1440x900: videos/images still render and generic fallback is hidden.
