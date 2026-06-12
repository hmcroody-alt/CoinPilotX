# PulseSoc Live Room Polish and Audio Feedback Report

Date: 2026-06-11

## Issues From Device QA

- Public viewer video showed permanent overlay text such as `Mux active`, score, category, and diagnostics.
- The `Tap to unmute` button was too large, centered over the video, and remained visible too long.
- The host could still see status text over the camera surface.
- A delayed repeat of the broadcaster audio could occur after roughly the HLS delay window.

## Root Cause

- The live video templates rendered health/status pills directly inside the video surface.
- Runtime transport diagnostics were visible to normal users.
- Mux HLS playback is delayed by design. If the broadcaster opens or hears the public HLS player while broadcasting, the delayed public audio can be captured again by the microphone and sent back to viewers.
- The public viewer page did not distinguish host-viewer playback from normal audience playback.

## Changes Made

- Removed live status/metric overlay pills from the Studio and public live video surfaces.
- Hid transport diagnostics unless a shell explicitly sets `data-live-debug="1"`.
- Changed `Tap to unmute` into a compact bottom-centered control.
- Added auto-subtle behavior so the unmute control fades if the viewer does not interact.
- Hides the unmute control immediately after audio is enabled.
- Forces host-viewer playback muted and hides the host-viewer unmute control to prevent delayed HLS audio from leaking back into the host microphone.
- Keeps the host local camera preview muted at the media element level.
- Suppressed scheduled Mux polling toast spam.
- Bumped the live runtime asset URL to force clients to fetch the patched script.

## Remaining Operational Note

If a broadcaster plays the public live room loudly on a separate nearby device, that external speaker audio can still be picked up by the microphone. The app now prevents the in-app host-viewer path from causing that loop, but physical speaker-to-mic feedback still requires headphones, device separation, or muting nearby playback.

## Validation

Passed:

- JavaScript parse
- Python compile
- Live Studio audit
- LiveKit/Mux bridge audit
- Mux truth audit
- Mux Live audit
- `git diff --check`
