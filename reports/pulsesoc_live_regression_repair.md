# PulseSoc Live Regression Repair

Date: 2026-06-11

## Problem

Real testing showed a regression in PulseSoc Live:

- Desktop Studio could show an active camera feed while the old idle overlay still said "Camera off".
- Public live room could fall back to the placeholder state even when a LiveKit/Mux live path was active or warming up.
- User-facing live metrics could show confusing "Mux active" labels.
- Native mobile live could get stuck because the app tried to hold VisionCamera preview and start LiveKit publishing at the same time, and long async calls could leave controls disabled.

## Root Cause

- The web camera overlay depended mostly on a CSS class. If script state, cached assets, or polling text updates diverged, the idle overlay and camera status toast could reappear over the real feed.
- Public playback render logic trusted the stored live status too much and did not promote a placeholder to HLS playback after polling saw a ready playback URL.
- Native mobile LiveKit startup had no timeout guard and did not deliberately release the VisionCamera preview before LiveKit requested the camera.

## Changes

- Added explicit web camera surface state helpers that hide the idle overlay with the \`hidden\` attribute and clear non-error camera toasts after camera activation.
- Kept only error camera status visible over Studio; normal publishing state no longer sits on the host face.
- Added client-side public playback promotion: the public room replaces the placeholder with the HLS video when state polling sees an active live and playback URL.
- Kept browser-safe unmute markup available while allowing host viewers to hide it via JS.
- Removed user-facing fallback labels that said "Mux active" in bitrate/FPS fields.
- Cache-busted the live runtime asset to force the fixed JavaScript to load.
- Hardened server active-live detection to consider Mux and LiveKit evidence, not just one stale \`status\` value.
- Changed the public placeholder copy to a connecting state when LiveKit/Mux is active but HLS is still warming up.
- Added native mobile timeouts for live creation, LiveKit token creation, room connection, camera start, microphone start, Mux forwarding, and live ending.
- Native mobile now flips to broadcasting state before LiveKit camera capture so VisionCamera releases the device and LiveKit can publish without hanging.
- Close and End Live remain reachable instead of being blocked by a stuck busy state.

## Affected Files

- \`bot.py\`
- \`static/js/pulse_live_studio_runtime.js\`
- \`static/js/pulse_live_studio.js\`
- \`static/css/pulse_live_studio.css\`
- \`mobile/pulse-react-native/components/NativeLiveBroadcast.tsx\`

## Validation

Passed:

- \`node --check static/js/pulse_live_studio_runtime.js\`
- \`node --check static/js/pulse_live_studio.js\`
- \`python3 -m py_compile bot.py\`
- \`npm run typecheck\` in \`mobile/pulse-react-native\`
- \`python3 scripts/pulse_livekit_mux_bridge_audit.py\`
- \`python3 scripts/pulse_mux_live_studio_truth_audit.py\`
- \`venv/bin/python scripts/live_studio_audit.py\`
- \`venv/bin/python scripts/mux_live_audit.py\`
- \`python3 scripts/live_mobile_audit.py\`
- \`npm run audit:native-live\`
- \`npm run audit:mobile-web-parity\`
- \`npm run audit:mobile-performance\`
- \`npm run audit:firebase\`
- \`npm run audit:store\`
- \`venv/bin/python scripts/live_viewer_playback_audit.py\`
- \`venv/bin/python scripts/live_audio_video_pipeline_audit.py\`
- \`venv/bin/python scripts/live_pipeline_audit.py\`
- \`git diff --check\`

Notes:

- \`scripts/live_studio_audit.py\`, \`scripts/mux_live_audit.py\`, \`scripts/live_viewer_playback_audit.py\`, \`scripts/live_pipeline_audit.py\`, and \`scripts/live_audio_video_pipeline_audit.py\` require the repo virtualenv because system Python lacks \`requests\`.
- This report does not claim a fresh real-device build was produced. It documents the code-level regression repair and local validation. A new iOS/Android build should be produced after deployment if the user wants this exact patch in TestFlight/Play testing.

