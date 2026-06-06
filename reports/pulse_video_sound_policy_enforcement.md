# Pulse Video Sound Policy Enforcement

Date: 2026-06-06

## Policy

Pulse video playback defaults to sound on. The single persisted preference is `pulseMediaSoundEnabled`.

- Missing key: treated as `true`.
- `true`: videos attempt unmuted playback.
- `false`: user manually chose muted playback.

Browser autoplay fallback may temporarily mute one blocked playback attempt, but it must not write `pulseMediaSoundEnabled=false`.

## Changes

- Updated the shared Pulse media renderer to remove `muted` markup, clear `defaultMuted`, and default sound preference to `true`.
- Updated Reels active-scroll playback to request sound instead of muted playback.
- Updated Reels fallback so blocked autoplay can retry muted without saving a long-term muted preference.
- Removed muted defaults from feed/status/composer/video-preview/live playback markup.
- Left local camera monitor previews muted because they are capture monitors, not published Pulse video playback.

## Covered Surfaces

- Feed videos and reposted Reel media rendered through the shared media renderer.
- Reels active playback and scroll-to-next playback.
- Pulse Videos/detail media rendered through shared player.
- Status viewer and status previews.
- Live public playback and live replays.
- Profile/saved/creator previews that use shared media or composer preview markup.

