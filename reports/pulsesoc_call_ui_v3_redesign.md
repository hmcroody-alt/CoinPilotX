# PulseSoc Call UI V3 Redesign

## What changed

PulseSoc Messenger now uses a cleaner conversation header and an immersive active-call screen.

The conversation header was reduced to:

- Back button
- Conversation avatar
- Conversation name and presence
- Audio call
- Video call
- More

The duplicate header search, gear/settings, info, and AI controls were removed from the conversation header. More opens the Conversation Control Center, where search and settings remain available.

## Active call UI

The active call screen was redesigned from a boxed control-heavy modal into a full-screen cinematic call stage.

Default visible controls:

- Remote video or audio visual
- Local self-preview for video calls
- Top call identity/status pill
- End button

Hidden overlay controls:

- Mic
- Camera
- Flip
- Speaker
- Minimize
- More

The controls appear on tap/click/mouse interaction and auto-hide after three seconds. The End button remains visible at all times outside the incoming-call state.

## Audio mode

Audio-only calls no longer depend on a blank video rectangle. They show a large animated PulseSoc audio orb, caller identity, timer, quality status, and the always-available End button.

## Video mode

Video calls prioritize the remote camera as the full-screen experience. The local self-preview floats near the top-right, with a neon border and camera-off fallback. Remote video uses `object-fit: cover` to avoid cramped letterboxed boxes during normal calls.

## Accessibility

The call buttons keep `aria-label` values for:

- End call
- Mute microphone
- Unmute microphone
- Turn camera off
- Turn camera on
- Flip camera
- Speaker
- Minimize call
- Restore call

The layout keeps 44px+ touch targets, visible focus states, safe-area support, and reduced-motion compatibility.

## Files changed

- `templates/pulse_messages_v2.html`
- `static/js/pulse_messages_v2.js`
- `static/pulsesoc_calls.js`
- `static/css/pulse_messages_v2.css`
- `scripts/pulsesoc_call_ui_v3_audit.py`
- `reports/pulsesoc_call_ui_v3_redesign.md`

## QA status

Static and local verification passed after implementation.

Commands run:

- `node --check static/pulsesoc_calls.js`
- `node --check static/js/pulse_messages_v2.js`
- `venv/bin/python -m py_compile scripts/pulsesoc_call_ui_v3_audit.py`
- `venv/bin/python scripts/pulsesoc_call_ui_v3_audit.py`
- `venv/bin/python scripts/pulsesoc_communications_engine_audit.py`
- `venv/bin/python scripts/pulsesoc_real_call_experience_audit.py`
- `venv/bin/python scripts/pulsesoc_calls_phase3_live_qa_audit.py`
- `venv/bin/python scripts/calls_backend_command_center_audit.py`
- `git diff --check -- templates/pulse_messages_v2.html static/js/pulse_messages_v2.js static/pulsesoc_calls.js static/css/pulse_messages_v2.css scripts/pulsesoc_call_ui_v3_audit.py reports/pulsesoc_call_ui_v3_redesign.md`
- `curl -fsS http://127.0.0.1:5069/health`

Browser QA:

- Desktop Messenger loaded the updated cache-busted CSS/JS assets.
- Header action cluster rendered exactly three controls: audio call, video call, More.
- Audio and video controls rendered as 48px touch targets, More rendered as a 46px touch target.
- Duplicate header search and gear/settings controls were absent.
- More opened the Conversation Control Center without route navigation.
- Desktop and mobile control-center checks showed no horizontal overflow.
- No local `127.0.0.1` console errors were captured during the smoke pass.

Real two-user camera/audio QA should still be repeated against the deployed build with two authenticated users to confirm remote media rendering and physical-device audio behavior after production cache refresh.

## Known limitations

- Speaker routing still follows browser/device support. Unsupported output selection is reported safely rather than faked.
- True native locked-screen call UI still depends on native platform integrations; this UI continues to use the existing PulseSoc Communications Engine and PWA/browser call path.
