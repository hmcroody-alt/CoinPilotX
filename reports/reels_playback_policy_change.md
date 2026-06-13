# Reels Playback Policy Change

Generated: 2026-06-13

## Change

Reels now follow the modern short-form playback policy:

- Active visible Reel autoplays without hover.
- Sound is on by default unless the user previously muted PulseSoc media.
- Tap toggles mute/unmute and stores the preference.
- Double tap/double click likes.
- Long press opens a reaction affordance.
- Next two Reels are preloaded to reduce black screens.
- Persistent sound bubbles are hidden; sound indicators are transient.

## Scope

Updated the shared media renderer so desktop hover preview no longer gates Reels playback. Non-Reels hover previews remain intact.

## Files

- `/Users/hmcherie/Desktop/CoinPilotX/bot.py`
- `/Users/hmcherie/Desktop/CoinPilotX/static/js/pulse_media_renderer.js`
- `/Users/hmcherie/Desktop/CoinPilotX/scripts/pulse_reels_playback_audit.py`
- `/Users/hmcherie/Desktop/CoinPilotX/scripts/pulse_all_video_sound_policy_audit.py`
