# Video Reels Status Playback Policy Report

Date: 2026-06-09

- Shared media renderer attempts unmuted autoplay for feed videos, reels, and videos, then falls back to muted playback only when browser policy blocks sound.
- Status viewer uses muted autoplay by policy with visible mute/unmute controls.
- IntersectionObserver pauses offscreen videos and avoids multiple audible videos.
- Nearby video metadata is preloaded.
- Mux HLS playback is centralized through the shared renderer.

Known platform limitation: iOS/Android/browser autoplay rules may still require a user gesture before audible playback.

