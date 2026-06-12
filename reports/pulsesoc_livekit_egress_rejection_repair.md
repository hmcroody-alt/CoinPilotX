# PulseSoc LiveKit Egress Rejection Repair

Updated: 2026-06-12T17:03:48Z

## Issue

Desktop and mobile live sessions regressed to a non-working state. The visible desktop failure was:

- `LiveKit egress was rejected.`
- Studio remained at `0 kbps` and `0 FPS`.
- Mux stayed idle.
- Mobile could appear stuck because the native flow did not surface Mux-forwarding failures.

## Root Cause

The backend only attempted LiveKit `StartRoomCompositeEgress` for Browser Live. For PulseSoc's one-host browser/mobile publishing flow, LiveKit may reject room-composite egress depending on room state, project egress support, or compositor constraints. The old implementation returned a generic failure and did not try the simpler host participant egress path.

## Fix

- Added `StartParticipantEgress` as the primary LiveKit-to-Mux bridge.
- Kept `StartRoomCompositeEgress` as a fallback.
- Added sanitized LiveKit rejection diagnostics for both paths.
- Returned the successful egress strategy to the Studio/mobile API.
- Kept Mux stream keys and secrets out of logs and API responses.
- Updated native mobile live flow so Mux forwarding failures are shown instead of silently swallowed.
- Updated live audits to enforce participant egress, room-composite fallback, and safe diagnostics.

## Safe Production Checks

Railway production variables were checked through a redacted boolean-only command:

- `LIVEKIT_URL`: loaded
- `LIVEKIT_API_KEY`: loaded
- `LIVEKIT_API_SECRET`: loaded
- `MUX_TOKEN_ID`: loaded
- `MUX_TOKEN_SECRET`: loaded
- `MUX_WEBHOOK_SECRET`: loaded
- `MUX_DATA_ENV_KEY`: loaded
- `MUX_SOURCE_BASE_URL`: loaded

No secret values were printed.

## Browser QA Note

The Browser automation plugin was initialized, but it returned no accessible tabs for this session. Live dashboard interaction could not be driven through that channel in this turn. Local and CLI validation completed, and the code now records exact safe rejection details for the next real live test.

## Validation

Passed:

- `python3 -m py_compile bot.py`
- `node --check static/js/pulse_live_studio_runtime.js`
- `node --check static/js/pulse_live_studio.js`
- `python3 scripts/pulse_livekit_mux_bridge_audit.py`
- `venv/bin/python scripts/live_media_transport_audit.py`
- `venv/bin/python scripts/live_audio_video_pipeline_audit.py`
- `venv/bin/python scripts/live_viewer_playback_audit.py`
- `venv/bin/python scripts/mux_live_audit.py`
- `npm run typecheck` in `mobile/pulse-react-native`
- `npm run audit:native-live` in `mobile/pulse-react-native`
- `python3 scripts/live_mobile_audit.py`

## Expected Result

On the next host start:

1. Browser/mobile publishes camera and microphone into the LiveKit room.
2. Backend starts participant egress to Mux RTMP.
3. If participant egress is rejected, backend tries room composite egress.
4. Studio/mobile shows safe exact failure details if both paths fail.
5. Mux should move from idle to active once egress succeeds.
