# PulseSoc Full-Screen Incoming Call Interruption

## What Changed

PulseSoc now has a global incoming-call interruption layer for authenticated PulseSoc surfaces. The existing PulseSoc Communications Engine and LiveKit client remain the single call system; this change makes that runtime available outside Messenger and forces incoming calls into a full-screen foreground overlay.

## Files Changed

- `static/pulsesoc_calls.js`
- `static/js/pulsesoc_global_call_overlay.js`
- `static/css/pulsesoc_global_call_overlay.css`
- `bot.py`
- `scripts/pulsesoc_fullscreen_incoming_call_audit.py`
- `reports/pulsesoc_fullscreen_incoming_call.md`

## Runtime Behavior

When an incoming call is received while PulseSoc is foregrounded:

1. The global overlay loader starts on authenticated `/pulse`, `/pulse/*`, and `/dashboard` pages.
2. It loads the existing call client, LiveKit browser bundle, and Communications V2 realtime stream.
3. Realtime `incoming_call`, `communication_call_incoming`, `call_started`, and notification fallback events are still handled by `static/pulsesoc_calls.js`.
4. Polling fallback still checks `/api/calls/active`.
5. The full-screen incoming call surface appears immediately.
6. Current page media is paused and drafts are preserved.
7. Ring tone and vibration are triggered where the browser allows them.
8. `ring-seen` is recorded once per incoming call.
9. Accept transitions into the existing LiveKit active call flow.
10. Decline posts the existing backend decline route and restores the previous screen state.

## Incoming UI

The foreground call screen uses:

- Full-screen dark PulseSoc call surface
- Animated pulse core
- Caller identity and connection type
- Large safe-area-aware Accept and Decline controls
- No overlapping buttons
- Reduced-motion support
- High z-index above Reels, Status, Home, Pulse AI, Alerts, Dashboard, and Messenger

## Lifecycle Fixes

The call client now emits lifecycle events:

- `pulsesoc:incoming-call`
- `pulsesoc:call-accepted`
- `pulsesoc:call-declined`
- `pulsesoc:call-terminal`
- `pulsesoc:call-interruption-ended`

Incoming calls now start status polling. This prevents stale ringing overlays when the caller ends, timeout marks the call missed, or the backend returns declined/canceled/failed.

Duplicate incoming refreshes are guarded so polling does not restart ringtone, re-pause video, or re-fire the global interruption event every few seconds.

## Background And Locked Screen

For background or locked phones, the browser/PWA must use the existing push notification route. Web apps cannot force a native full-screen CallKit interruption on iOS unless a native app integration provides that capability. The foreground path is now full-screen; locked/background delivery remains dependent on push permission, OS notification settings, and platform support.

## QA Results

Static verification confirms:

- Global call overlay script exists and loads the existing call client.
- Global overlay CSS provides full-screen incoming call layout.
- Communications V2 realtime stream is connected globally.
- Polling fallback remains active.
- Accept/decline/ring-seen/end routes remain wired.
- Incoming calls record `ring-seen`.
- Incoming calls start status polling for missed/canceled/caller-ended cleanup.
- Current media pause and draft preservation are implemented in the global layer.
- Authenticated PulseSoc pages receive camera/microphone permissions needed after accept.

Manual two-device foreground QA still needs to be run on production devices for:

- Reels foreground interruption
- Status foreground interruption
- Home foreground interruption
- Messenger draft restore
- Pulse AI foreground interruption
- Locked/background push behavior

## Remaining Limitations

- Native locked-screen full-screen CallKit behavior is not available from the web/PWA alone.
- Browser autoplay policies can limit ringtone until the user has interacted with the site.
- OS-level notification permissions cannot be forced by PulseSoc.

## Next Steps

1. Run two-device incoming call QA across Reels, Status, Home, Messenger, Pulse AI, and Dashboard.
2. Verify locked-screen push still opens the exact call or conversation deep link.
3. If native full-screen locked-call interruption is required on iOS, add native app CallKit integration.
