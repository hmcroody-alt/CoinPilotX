# PulseSoc Reels Startup And Audio Policy Repair

Date: 2026-06-13

## Scope

This repair targets the PulseSoc Reels playback policy on web and mobile webview surfaces:

- Active Reel starts immediately when visible.
- Audio defaults ON when no user mute preference exists.
- Hover is not required.
- Tap toggles mute/unmute.
- Double tap keeps like behavior.
- Next two Reels preload their video source.

## Root Cause

1. Startup relied on a single `requestAnimationFrame(syncPlayback)` after Reels were rendered. If media hydration, HLS setup, or browser readiness lagged that one frame, the active Reel could remain on poster/spinner until scroll or another event triggered playback.
2. The unmuted autoplay fallback could mute the active video after browser autoplay rejection while the saved sound preference still said sound ON. The next user tap then ran the normal mute toggle and could save muted mode instead of unlocking sound.
3. The second preloaded Reel used `metadata` only, so swipe-to-next could still wait on source setup.
4. The persistent sound control could show `Muted`, making temporary autoplay fallback look like a saved user preference.

## Changes

- Added `scheduleReelsPlayback()` to re-run active playback after render, `pageshow`, resize, visibility restore, and `canplay`.
- Active Reel now uses eager `preload='auto'`, calls `load()` when needed, removes the muted attribute, sets `volume=1`, and attempts unmuted playback first.
- If the browser blocks unmuted autoplay, Reels fall back to muted playback only for that autoplay attempt and mark the card with `data-reel-autoplay-blocked`.
- A tap on a blocked Reel now unlocks sound, persists sound ON, and retries unmuted playback instead of toggling to muted.
- Next two Reels now both preload video source with `preload='auto'`.
- The always-visible sound control now uses neutral `Audio` text. `Muted` is transient only after a user mute; `Tap for sound` appears only after an autoplay block.

## Files Changed

- `bot.py`
- `scripts/pulse_reels_playback_audit.py`
- `reports/reels_startup_audio_policy_repair.md`

## Expected Timing

- First Reel startup target: under 500ms when API/media are already reachable and the browser allows autoplay.
- If browser policy blocks unmuted autoplay, playback still starts muted and displays one actionable `Tap for sound` prompt.
- Swipe transitions should avoid black screens because N+1 and N+2 are loaded.

## QA Results

- `node --check` passed for the extracted inline Reels page script.
- `python3 -m py_compile bot.py scripts/pulse_reels_playback_audit.py` passed.
- `python3 scripts/pulse_reels_playback_audit.py` passed.
- `python3 scripts/pulse_all_video_sound_policy_audit.py` passed.
- `python3 scripts/protection/run_protection_suite.py` passed.
- `git diff --check -- bot.py scripts/pulse_reels_playback_audit.py reports/reels_startup_audio_policy_repair.md` passed.

## Browser QA Notes

Production baseline in the in-app Chromium browser confirmed the deployed bug before this patch:

- URL: `https://pulsesoc.com/pulse/reels?tab=for_you`
- Cards after async load: `11`
- Active Reel state: `paused=true`, `muted=true`, `defaultMuted=true`
- Persistent control label: `Muted`
- Active startup metric: not present
- Next two Reels preload state: not active
- Evidence: `reports/pulsesoc-evidence/reels-production-baseline-muted-2026-06-13.png`

Local browser QA was limited by the local fixture feed returning zero Reels for the authenticated local QA user. The local API itself responded successfully in `302.2ms`, so the zero-card result is fixture data, not the playback patch. The playback policy is therefore validated by code-level browser-accessible JavaScript parsing and the Reels/media protection audits before deployment. A follow-up production browser check should be run after this commit deploys to confirm the same page now reports active playback with `defaultMuted=false`, `muted=false` when the browser allows unmuted autoplay, or `data-reel-autoplay-blocked=1` with the one-time `Tap for sound` prompt when browser policy blocks sound.
