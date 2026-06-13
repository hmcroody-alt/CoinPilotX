# Reels Tap Controls Repair Report

## Scope

Fixed mobile Reels tap behavior so media taps no longer expose the broken center play/pause overlay.

## Root Causes Found

- The hidden center play control could still be reached through the older generic Reels playback click handler.
- Some mobile taps landed on the parent `.reel-card` instead of `.reels-media-stage`, so the first gesture guard missed them.
- Double tap already reached the reaction path, but it could coexist with the old playback controls state.

## Files Modified

- `bot.py`
- `scripts/pulse_reels_tap_controls_audit.py`

## Fix Applied

- Added a small `.reel-tap-sound-icon` overlay for clean mute/unmute feedback.
- Suppressed `.reel-center-play` on mobile so broken center text cannot render.
- Added a shared Reels gesture guard that treats taps inside the media-stage rectangle as media taps even when the DOM target is the parent card.
- Single tap now toggles mute/unmute only and removes `show-controls`.
- Double tap now likes/unlikes through the existing optimistic `fireReel` backend flow, with floating emoji burst feedback.
- Preserved explicit sound/play controls outside the mobile media tap path.

## QA Browser Results

| Test | Result | Evidence |
| --- | --- | --- |
| iPhone viewport `390x844` loads Reels | PASS | `.reels-media-stage` fills `390x844`; horizontal overflow `0` |
| iPhone single tap toggles sound only | PASS | `.reel-tap-sound-icon` visible with `🔊`; `.reel-center-play` display `none`; `show-controls=false` |
| iPhone double tap likes Reel | PASS | reaction active `true`; count `1`; floating emoji burst present; `show-controls=false` |
| Android viewport `412x915` loads Reels | PASS | `.reels-media-stage` fills `412x915`; horizontal overflow `0` |
| Android single tap toggles sound only | PASS | `.reel-tap-sound-icon` visible with `🔊`; `.reel-center-play` display `none`; `show-controls=false` |
| Android double tap likes Reel | PASS | reaction active `true`; count `1`; floating emoji burst present; `show-controls=false` |
| Action buttons remain icon-based | PASS | visible stack uses emoji/icon buttons and no chopped text |
| Follow button remains compact | PASS | follow button width `68px`, near creator info |

## Screenshots

- `reports/pulse_reels_tap_controls_after_iphone_390x844.png`
- `reports/pulse_reels_tap_controls_after_android_412x915.png`

## Validation

- `python -m py_compile bot.py scripts/pulse_reels_tap_controls_audit.py scripts/pulse_reels_action_buttons_audit.py scripts/pulse_reels_follow_button_audit.py`
- `python scripts/pulse_reels_tap_controls_audit.py`
- `python scripts/pulse_reels_action_buttons_audit.py`
- `python scripts/pulse_reels_follow_button_audit.py`
- `git diff --check`

Temporary local QA data was inserted for browser testing and removed before commit.
