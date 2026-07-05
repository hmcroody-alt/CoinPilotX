# PulseSoc Native Full-Screen Incoming Calls Foundation

Date: 2026-07-05

## Scope

This foundation adds foreground incoming-call takeover behavior to the parallel native app. It does not change production WebView routes, production APNs/FCM credentials, or backend call business logic.

## Backend Reuse

Existing PulseSoc systems reused:

- `GET /api/calls/active`
- `GET /api/calls/<call_id>/status`
- `POST /api/calls/<call_id>/ring-seen`
- `POST /api/calls/<call_id>/accept`
- `POST /api/calls/<call_id>/decline`
- `POST /api/calls/<call_id>/end`
- Existing notification/deep-link routing.
- Existing `CallScreen`.
- Existing LiveKit connection layer.
- Existing Communications V2 authorization, call state, participants, call events, and notification delivery behavior.

The native app does not decide call eligibility, visibility, participants, or delivery. The backend remains authoritative.

## Implemented

- Added app-shell `IncomingCallLayer` mounted above the signed-in navigator.
- Foreground incoming calls now interrupt the current native screen with a full-screen PulseSoc call surface.
- The layer refreshes from `/api/calls/active` while the app is active, on app resume, and when a foreground notification is received.
- The incoming UI displays caller identity, call type, call state, large avatar, animated orbital energy fields, and PulseSoc-styled accept/decline controls.
- Accept routes into the existing native `CallScreen`.
- Decline calls the existing backend decline endpoint.
- Silent ignore and Remind me later suppress the foreground overlay locally without changing backend call state.
- Active connected calls can appear as a floating call bubble when browsing other native screens.
- The floating call bubble can reopen the existing `CallScreen` or end the call through the backend.
- `CallScreen` minimize now reports the backend minimize state and returns to the previous screen so the floating bubble can keep the call accessible.

## Design Notes

The UI follows the internal PulseSoc futuristic design standard with depth, motion, luminous rings, and a premium full-screen interruption. The internal LogiNexus concept is not exposed as user-facing copy.

## Practical QA Status

Verified statically:

- The incoming-call layer is mounted in `App.tsx` for signed-in users.
- It reuses `/api/calls/active`, ring-seen, accept, decline, and end APIs.
- It routes answered calls into `CallScreen`.
- It provides silent ignore, remind-later, and floating bubble behavior.
- It does not modify production WebView files.
- User-facing native source does not contain visible `LogiNexus` copy.

Verified through Expo web route boot:

- `npm run web:qa` bundled successfully.
- `curl -I http://localhost:8094/pulse/calls/test-call` returned `HTTP/1.1 200 OK`.
- This verifies route serving and bundle compatibility only; it does not prove authenticated incoming-call state or native device media behavior.

Not verified in this mission:

- Real APNs/FCM incoming push delivery.
- OS lock-screen/full-screen presentation.
- Two-device LiveKit audio/video call quality.
- Bluetooth, speaker route, and background audio behavior.
- Physical iPhone/Android incoming-call timing.

These remain release blockers, not development blockers.

## Next Recommended Action

Continue development unless a critical/security/data-loss/production-breaking issue appears. The highest-value follow-up is a practical incoming-call QA sweep against a seeded active-call fixture or a local two-account backend test, then continue toward the next native feature gap.
