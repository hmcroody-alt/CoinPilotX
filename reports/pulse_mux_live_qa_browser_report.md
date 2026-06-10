Pulse Mux Live QA Browser Report

QA expectations:
- `/pulse/live/studio/34` should show Browser Live as the primary LiveKit-to-Mux path.
- OBS/RTMP instructions should remain available only as an advanced backup.
- Host-only stream key must be masked by default and visible/copyable only in Studio.
- Copy buttons should copy host ingest values.
- Mux status polling should move out of idle after Browser Live publishes to LiveKit and LiveKit Egress forwards to Mux.
- Public viewer should not show fake live status while Mux is idle.

QA browser note:
- The in-app browser could not open local `localhost`, `127.0.0.1`, or `0.0.0.0` routes during this pass because the browser reported `net::ERR_BLOCKED_BY_CLIENT`.
- Local route, UI, security, and performance coverage was validated through Flask test-client audits and JavaScript/CSS static audits instead.

If no Browser Live or OBS/RTMP source is available:
- Mux remains idle by design.
- The UI must explain that Browser Live publishes through LiveKit and forwards to Mux, while OBS/RTMP is optional advanced mode.
