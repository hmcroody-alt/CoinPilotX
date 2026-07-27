# PulseSoc Native Live Audio and Viewer Playback Repair

Date: 2026-07-26
Branch: `release/undx-nexus-core-v4`

## Result

Code repair complete. No TestFlight build, EAS build, Transporter upload, App Store Connect submission, or tester assignment was performed.

## User-Observed Defects

- Host video was visible in the native Reels/Live feed, but host audio was not audible.
- Tapping `Join Live` could open the full native Live screen in a `STARTING` state with `Live playback unavailable`.
- The native screen could offer `Open Live Web Viewer` even when the Live session was active enough for native LiveKit playback.
- Reels and full Live detail could disagree about session state.

## Root Cause

- The dedicated native Live viewer only attempted LiveKit connection when the state payload already exposed native WebRTC support. If the Live state endpoint did not serialize a room/HLS-capable playback manifest yet, the viewer fell through into the unsupported web-fallback state instead of asking the existing LiveKit token route for viewer credentials.
- The native host screen published local media into LiveKit but did not confirm published audio/video tracks through a server route. Backend state could therefore remain `starting` even after native media was available.
- The backend serialized LiveKit-direct sessions as `starting` when HLS/Mux egress had not become active yet, even if the LiveKit room had published tracks.
- HLS playback in the native viewer defaulted to muted, which could make supported HLS sessions look silent.

## Repair

- Added a native publish endpoint alias at `/api/pulse/live/<id>/native-publish` while preserving `/browser-publish` for existing production clients.
- Added `confirmHostLivePublish()` in native Live API code and wired the native host session to confirm local audio/video track publication after LiveKit connection.
- Updated backend publish confirmation so LiveKit-direct sessions with verified tracks are marked `live` even when Mux/HLS egress is unavailable or quota-limited.
- Updated Live state serialization and Reels Live item mapping to expose LiveKit-direct active sessions as `live`.
- Allowed Reels discovery to include WebRTC-only native Live sessions without requiring HLS.
- Updated the full native Live viewer to prefer LiveKit for active sessions, request viewer credentials directly, enable remote audio, and use native HLS only as a supported fallback when LiveKit credential resolution fails.
- Changed native HLS default audio from muted to sound-on.

## Validation

- Python compile: `bot.py`, `services/live_distribution_service.py` passed.
- TypeScript: `npm run --prefix mobile-native typecheck` passed.
- Native Jest: `82` suites passed, `1306` tests passed.
- Focused Live Jest: `3` suites passed, `15` tests passed.
- Expo Doctor: `16/16` checks passed with `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0`.
- `scripts/pulsesoc_native_live_webrtc_guest_audio_repair_audit.py`: passed.
- `scripts/pulsesoc_native_live_audit.py`: passed.
- `scripts/live_audio_audit.py`: passed.
- `scripts/live_distribution_audit.py`: passed.
- `scripts/livekit_mux_egress_gate_audit.py`: passed.
- `scripts/pulse_mux_live_mobile_audit.py`: passed.
- `tests/protection/test_livestream_contract.py`: passed when run directly with `.venv/bin/python`.
- `git diff --check`: passed.

## Notes

- `.venv/bin/python -m pytest tests/protection/test_livestream_contract.py -q` could not run because `pytest` is not installed in `.venv`; the same protection contract is a standalone script and passed directly.
- `scripts/live_viewer_playback_audit.py` is a legacy web fallback audit for `/pulse/live/<id>`. It currently receives a `302` redirect to `/pulse/reels?live=<id>`, so it does not validate the native viewer repair path.

## QA Classification

- Code-path verified: host publish confirmation, native LiveKit viewer token request, WebRTC-only active state normalization, remote audio re-enable path, HLS fallback path.
- Automated-audit verified: native Live playback/audibility contracts and backend distribution contracts.
- Simulator verified: not performed in this mission because no local iOS build/install was needed for the isolated code repair path.
- Physical-device-only: real host microphone capture, remote viewer audibility, guest/co-host live audio, Bluetooth/speaker routing, interruption behavior, and multi-device LiveKit media must still be verified on physical iPhones before declaring release readiness.

## Release Judgment

- Native code repair: PASS.
- TestFlight release: NOT ATTEMPTED by constraint.
- Native Live ready for a future TestFlight build: CODE READY, pending the broader collected defect list and physical two-device/three-device QA.
