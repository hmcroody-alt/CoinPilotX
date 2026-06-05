Pulse Mux Live QA Browser Report

QA expectations:
- `/pulse/live/studio/34` should show OBS/RTMP instructions.
- Host-only stream key must be visible only in Studio.
- Copy buttons should copy host ingest values.
- Mux status polling should show idle until OBS/RTMP connects.
- Public viewer should not show fake live status while Mux is idle.

QA browser note:
- The in-app browser could not open local `localhost`, `127.0.0.1`, or `0.0.0.0` routes during this pass because the browser reported `net::ERR_BLOCKED_BY_CLIENT`.
- Local route, UI, security, and performance coverage was validated through Flask test-client audits and JavaScript/CSS static audits instead.

If no OBS/RTMP source is available:
- Mux remains idle by design.
- The UI must explain that no broadcast is active until an RTMP encoder connects.
