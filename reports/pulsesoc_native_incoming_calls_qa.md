# PulseSoc Native Incoming Calls Practical QA

Date: 2026-07-05

## Scope

This was a practical QA pass for the foreground Native Incoming Calls foundation. It was intentionally not a release-level APNs/FCM, lock-screen, or two-device LiveKit media certification.

## Fixture Path

Added a dev/local-only seeded active-call fixture:

- Source: `mobile-native/src/calls/incomingCallQa.ts`
- Enabled only when `__DEV__` is true and the current URL or API base is local.
- Supports:
  - `?qa_incoming_call=1`
  - `?qa_active_call=1`
  - `call_id`
  - `caller`
  - `call_type=audio|video`

This does not weaken production auth, production provider behavior, or production WebView routes.

## Checks

Verified by static/audit inspection:

- Incoming-call layer mounts above any signed-in native screen through `App.tsx`.
- Full-screen overlay can be seeded by a dev/local QA fixture.
- Active-call floating bubble can be seeded by a dev/local QA fixture.
- `ring-seen` now fires once per call per foreground session instead of every poll.
- Accept routes into `CallScreen`.
- Decline calls the backend decline endpoint.
- Floating-bubble End calls the backend end endpoint.
- Silent ignore suppresses the current call locally.
- Remind me later suppresses the current call locally.
- Minimized call returns to the previous screen and can be restored through the floating bubble.
- `/pulse/calls/:callId` deep-link route remains registered.
- Notification routing still routes `/pulse/calls/<call_id>` and message links with `call_id`.
- Loading/error states remain non-crashing and visible.
- User-facing native source does not expose `LogiNexus` copy.

Verified through local web route:

- `npm run web:qa` bundled the app.
- `curl -I http://localhost:8094/pulse?qa_incoming_call=1&call_id=qa-call-1&caller=PulseSoc%20QA&call_type=video` returned `HTTP/1.1 200 OK`.

Verified through built-in QA browser:

- Navigated to `http://localhost:8094/pulse?qa_incoming_call=1&call_id=qa-call-1&caller=PulseSoc%20QA&call_type=video`.
- The browser session was signed out, so the app correctly stayed on the auth gate.
- The incoming overlay was not visually verified in the browser because the layer is signed-in only and no authenticated QA browser session or local two-account backend was active.

## Fixes Made

- Added dev/local-only seeded incoming/active call fixture support.
- Fixed `ring-seen` so it is acknowledged once per call ID per foreground session.

## Not Verified

- Authenticated browser overlay rendering.
- Real local two-account incoming call.
- Backend accept/decline/end side effects against a live seeded call.
- Physical-device foreground overlay timing.
- Real notification tap into a ringing call.
- APNs/FCM delivery.
- Lock-screen/full-screen OS-level incoming-call behavior.
- Two-device LiveKit media quality.
- Bluetooth/speaker/background audio behavior.

These remain release blockers, not current development blockers unless they expose critical/security/data-loss/production-breaking behavior.

## Result

No critical, security, data-loss, or production-breaking issue was found. One scoped QA issue was fixed: duplicate `ring-seen` calls across repeated active-call polling.

## Recommendation

Continue development. The next highest-value action is to inspect the current native parity gaps and either:

- run a local authenticated two-account incoming-call QA pass if credentials/backend fixture are available, or
- proceed to the next buildable native feature if the two-account call fixture is not available.
