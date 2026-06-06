# Pulse No Forced Mute Audit

Date: 2026-06-06

## Findings

The forced mute behavior came from two main paths:

- Shared media renderer generated video markup with `muted` and set `defaultMuted=true` during hydration.
- Reels scroll playback called `playReelVideo(v,false)`, which re-applied muted playback when the active Reel changed or resynced.

## Fix

- Shared renderer now generates video elements without `muted`.
- Shared renderer clears `defaultMuted` before playback.
- Reels now call `playReelVideo(v,true)` for active videos.
- Browser fallback uses temporary muted retry only and does not save muted preference.
- User volume/mute actions still persist through `pulseMediaSoundEnabled`.

## Guardrails

Added/updated audits:

- `scripts/pulse_no_forced_mute_audit.py`
- `scripts/pulse_all_video_sound_policy_audit.py`
- `scripts/reels_sound_persistence_audit.py`
- `scripts/feed_video_sound_persistence_audit.py`
- `scripts/status_video_sound_persistence_audit.py`

