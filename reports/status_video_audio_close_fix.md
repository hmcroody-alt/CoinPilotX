# Status Video Audio And Close Fix

Date: 2026-06-06

## Changes

- Status viewer playback now reads the shared Pulse media sound preference through `PulseMediaRenderer.soundEnabled()`.
- If the saved preference is sound-on, Status videos attempt unmuted playback first.
- If the browser blocks unmuted autoplay, that attempt falls back to muted playback without saving a muted preference.
- The Status sound button saves the shared preference with `PulseMediaRenderer.setSoundEnabled()`.
- The close button now stops media, clears the progress timer, hides the viewer, and falls back to `/pulse` when there is no history route.
- Close tap handling stops propagation so media overlays cannot intercept it.
- CSS raises the close control above viewer media and optimizes it for mobile taps.

## Validation Targets

- `scripts/status_video_sound_audit.py`
- `scripts/status_viewer_close_button_audit.py`
- `scripts/status_video_layout_audit.py`
- Python compile
- JavaScript parse
- Mobile browser smoke QA

## Trace Note

The requested Communications V2 trace `3dac75efd9f3` was not present in the local `coinpilotx.log`, so the original production stack trace could not be extracted from this workspace.
