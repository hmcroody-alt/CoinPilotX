# Pulse Video Autoplay Preview

## Preview behavior

- Desktop hover starts a muted video preview.
- Hover exit pauses the preview and returns preload to metadata.
- Scroll/in-view autoplay uses `IntersectionObserver` and starts only when a video is mostly visible.
- Mobile uses the same in-view muted preview path.
- Desktop hover takes priority over scroll autoplay.
- Only one preview plays at a time.

## Safety and performance

- Autoplay is muted by default.
- Reduced motion and browser data saver settings disable preview autoplay when detectable.
- Cards render poster/thumbnail first and preload metadata only.
- HLS playback prefers native HLS where available.
- HLS.js is limited to one active instance and is destroyed on page hide or when the active video node is removed.

## Detail sizing

The shared Pulse video stage now reserves a fixed immersive frame before media loads:

- Desktop feed/detail/video-library frame: `clamp(650px, 72vh, 800px)`.
- Mobile feed/detail/video-library frame: `clamp(60vh, 68vh, 75vh)`.
- Video content uses `object-fit: contain`, so landscape, portrait, square, and long-form videos remain fully visible and centered.
- Letterboxing/background space is intentional and no longer collapses to the intrinsic video ratio.
- Reels and Status keep their full-screen cover behavior because those surfaces are designed as immersive vertical/story canvases.
