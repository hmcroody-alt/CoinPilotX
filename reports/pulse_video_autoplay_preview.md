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

The video detail player now uses a single-column Pulse layout and a large `16:9` frame up to `1172px` wide, preserving black letterbox space around contained video so the total player frame matches the intended larger visual footprint.
