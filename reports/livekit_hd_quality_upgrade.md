# PulseSoc LiveKit HD Quality Upgrade

## What Changed

- Upgraded Messenger video call LiveKit room setup to use `adaptiveStream`, `dynacast`, publish defaults, simulcast video layers, HD capture constraints, and audio processing defaults.
- Upgraded PulseSoc Live host/cohost camera capture with a 1080p-first fallback ladder for hosts and 720p-first fallback for cohosts.
- Added layout-aware remote subscription quality selection so active full-screen call video requests the high layer, while minimized calls can use a lower layer.
- Tuned the browser WebRTC live fallback publisher with a 30 fps HD bitrate envelope instead of leaving sender parameters fully implicit.
- Extended call quality telemetry to submit capture/rendered resolution, fps, bitrate, RTT, jitter, packet loss, codec, and remote quality intent.
- Added backend HD quality policy and admin-only quality test diagnostics.
- Added Calls Command Center quality summary cards for capture, rendered output, network, and codec.

## Files Changed

- `static/pulsesoc_calls.js`
- `static/js/pulse_live_studio_runtime.js`
- `services/pulsesoc_communications_engine.py`
- `pulse_communications_v2/routes.py`
- `templates/admin_calls_command_center.html`
- `scripts/livekit_hd_quality_audit.py`
- `reports/livekit_hd_quality_upgrade.md`

## LiveKit Quality Policy

Video calls:

- Default capture target: `1280x720@30`
- Maximum capture bounds: `1920x1080@30` when available
- Publish bitrate target: `2.5 Mbps`
- Audio processing: echo cancellation, noise suppression, auto gain control
- Active full-screen remote video: high subscription layer
- Minimized call: medium subscription layer

Live host:

- Preferred capture target: `1920x1080@30`
- Fallback ladder: `1280x720@30`, `960x540@24`, `640x480@20`
- Publish bitrate target: `4.2 Mbps`
- Simulcast layers: low, medium, high

## Mux Boundary

Real-time call and live quality is controlled by LiveKit capture, publish settings, subscription layer selection, network adaptation, and UI rendering.

Mux replay quality should be tuned only after confirming LiveKit is sending an HD input track. The admin quality policy now documents that boundary so replay quality is not blamed before LiveKit input quality is verified.

## Admin Diagnostics

Added:

- `POST /api/admin/calls/quality-test`
- `/admin/calls/quality-test`
- Quality summary in the call inspector

The diagnostics show presence and measured media quality only. LiveKit secrets, access tokens, and provider credentials are not rendered.

## QA Results

Passed locally:

- `node --check static/pulsesoc_calls.js`
- `node --check static/js/pulse_live_studio_runtime.js`
- `venv/bin/python -m py_compile bot.py services/*.py pulse_communications_v2/routes.py scripts/livekit_hd_quality_audit.py`
- `venv/bin/python -m py_compile services/pulsesoc_communications_engine.py pulse_communications_v2/routes.py scripts/livekit_hd_quality_audit.py`
- `venv/bin/python scripts/livekit_hd_quality_audit.py`
- `venv/bin/python scripts/pulsesoc_communications_engine_audit.py`
- `venv/bin/python scripts/pulsesoc_real_call_experience_audit.py`
- `venv/bin/python scripts/pulsesoc_calls_phase3_live_qa_audit.py`
- `git diff --check`
- `curl -fsS http://127.0.0.1:5069/health`
- `curl -fsS http://127.0.0.1:5069/health/live`
- `curl -fsS http://127.0.0.1:5069/health/ready`

Manual real-device QA still required:

- iPhone to iPhone video call HD check
- iPhone to desktop video call HD check
- Desktop to iPhone video call HD check
- Live host in Reels active viewer quality check
- Mux replay check after an HD LiveKit input is confirmed
- Weak-network downshift and recovery check

## Remaining Limitations

- This local environment cannot prove real device camera sensor resolution, carrier network behavior, or LiveKit selected layer in production without an active two-device session.
- Browser and iOS background behavior still depend on OS permissions and lifecycle policies.
- Mux egress/replay must be verified with a real live session after LiveKit input resolution is observed in diagnostics.
