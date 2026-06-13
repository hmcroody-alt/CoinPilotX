# PulseSoc Livestream Audio Echo Repair

Date: 2026-06-13

## Scope

This pass addresses host-side audio feedback and duplicate LiveKit microphone publication without changing the Live Studio layout.

## Root-Cause Risks Found

1. Camera startup did not have a shared in-flight lock across desktop and mobile controls, allowing rapid taps to race.
2. Reconnect/start paths did not explicitly unpublish and stop every existing local publication before capturing replacement tracks.
3. Publishing relied on normal flow rather than rejecting an already-published audio/video kind immediately before `publishTrack`.
4. The host preview was muted in markup but did not enforce zero output volume at runtime.
5. A host opening the public viewer route could reach viewer sound controls; host-viewer playback now has a permanent silent guard.

These were concrete feedback and duplication risks. A real two-device audio test is still required to identify which one caused the reported production echo.

## Implementation

- Host preview enforces `muted`, `defaultMuted`, `volume = 0`, `playsInline`, and mobile inline playback attributes.
- Microphone capture uses echo cancellation, noise suppression, and automatic gain control.
- Start Camera and screen-share operations use single in-flight promises and disable all matching controls while connecting.
- Reconnect/start cleanup unpublishes existing local tracks with stop enabled, stops remaining tracks, and disconnects stale rooms when requested.
- Track publication checks existing local publication kinds and refuses duplicate audio/video publication.
- Host-side public playback cannot be unmuted and is re-silenced on any volume change.
- General videos and Reels preserve sound-first autoplay with browser-policy fallback.
- Status viewers and previews autoplay muted and unmute only after an intentional media/sound tap.

## Validation

PASS:

- `scripts/live_audio_echo_prevention_audit.py`
- `scripts/pulse_livekit_mux_bridge_audit.py`
- `scripts/live_audio_video_pipeline_audit.py`
- `scripts/livekit_mux_egress_gate_audit.py`
- `scripts/live_media_transport_audit.py`
- `scripts/live_viewer_playback_audit.py`
- `scripts/live_egress_quota_fallback_audit.py`
- Reels/video/status playback-policy audits
- JavaScript syntax check
- Python compile check
- `git diff --check`

BLOCKED / NOT PASSED:

- Real host microphone to second-device viewer audio test.
- Reconnect audio test with a real microphone.
- Repeated start/stop camera test against a real LiveKit room.
- LiveKit egress to Mux confirmation for this patch.

## Exact External Blockers

- The LiveKit Cloud project displayed a free-tier/egress quota exceeded state during the production configuration verification on 2026-06-13.
- The in-app QA browser did not grant camera/microphone access, so it cannot publish a real microphone track.
- The local code cannot be tested in production before deployment, while the requested release gate prohibits committing/deploying before the real audio test.

## Release Gate

No commit or push should occur until all of the following are captured:

1. Host publishes exactly one audio track and one video track.
2. A separate browser/device receives one clean voice with no delayed host monitoring.
3. Host refresh/reconnect still publishes exactly one audio track.
4. Repeated Start Camera operations do not increase published audio-track count.
5. LiveKit egress quota is available and Mux changes from idle to active.

