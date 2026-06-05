# Pulse Voice Notes Mobile Validation

## Covered

- iPhone Safari and Android Chrome are supported through `navigator.mediaDevices.getUserMedia` and `MediaRecorder` when available.
- Permission denial shows a clear error instead of a silent failure.
- The recorder panel is compact and fits the Communications V2 mobile full-screen chat.
- The composer remains bottom anchored with safe-area padding.
- Pause/resume/stop/discard controls stay touch-friendly.
- Orientation changes keep the recorder in the same chat mode because state is client-side and the layout uses responsive CSS.

## Known Browser Behavior

Safari support depends on the installed iOS/macOS MediaRecorder implementation. If the browser does not expose MediaRecorder, the UI reports that voice recording is unavailable rather than showing a broken control.

## QA Result

Static mobile audits verify visible controls, no mobile overflow patterns, and no call/WebRTC scope leakage.
