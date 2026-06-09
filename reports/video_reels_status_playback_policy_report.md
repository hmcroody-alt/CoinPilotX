# Video Reels Status Playback Policy Report

Date: 2026-06-09

- Shared media renderer attempts unmuted autoplay for feed videos, reels, and videos, then falls back to muted playback only when browser policy blocks sound.
- Status viewer uses muted autoplay by policy with visible mute/unmute controls.
- IntersectionObserver pauses offscreen videos and avoids multiple audible videos.
- Nearby video metadata is preloaded.
- Mux HLS playback is centralized through the shared renderer.
- Mobile sound-enable clicks now route through `window.PulseMediaRenderer?.playVisibleVideo?.(...)`, keeping mobile WebView behavior tied to the shared playback policy.
- The generic Pulse shell now loads the mobile Reels guard stylesheet with the current cache key: `pulse_reels_experience.css?v=mobile-lock-20260603`.

Second-pass validation:

- `pulse_mobile_video_playback_audit.py`
- `pulse_mobile_hls_support_audit.py`
- `pulse_mobile_layout_regression_audit.py`
- `pulse_all_video_sound_policy_audit.py`
- `pulse_video_scroll_autoplay_audit.py`
- `video_playback_reliability_audit.py`

Known platform limitation: iOS/Android/browser autoplay rules may still require a user gesture before audible playback.
