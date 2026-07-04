# PulseSoc Native Live Viewer Device QA + Hardening

Date: 2026-07-04

## Scope

This checkpoint hardens the native Live discovery/viewer foundation only.

It does not build native Go Live, Studio, hosting, co-hosting, calls, camera publishing, microphone publishing, restream controls, or native LiveKit host controls. Those flows remain on the existing safe web fallback.

## Device QA Status

Real-device/simulator QA was not completed in this shell.

Reason:

- `xcrun` exists, but `xcrun simctl` is unavailable: `xcrun: error: unable to find utility "simctl", not a developer tool or in PATH`.
- `adb` is not available in PATH.

The following remain not device-verified:

- Live playback smoothness and audio behavior.
- HLS/Mux playback on iOS and Android.
- LiveKit direct fallback behavior on device.
- Chat keyboard ergonomics on small screens.
- Foreground/background recovery on real devices.
- Notification tap behavior on locked/unlocked devices.
- Long-running memory, heat, and battery behavior.

## Static/Local QA Completed

Verified through code inspection and local checks:

- Live discovery uses the existing `/api/pulse/live-now` endpoint.
- Live state refresh uses the existing `/api/pulse/live/<id>/state` endpoint.
- Viewer join uses the existing `/api/pulse/live/<id>/join` endpoint.
- Live chat uses the existing `/api/pulse/live/<id>/chat` endpoint.
- Live reactions use the existing `/api/pulse/live/<id>/react` endpoint.
- Go Live and Studio remain web fallback.
- Co-hosting remains out of scope.
- Native LiveKit host token and browser-publish flows are not called by native.
- Production WebView paths were not modified.

## Hardening Completed

Added foreground/background recovery:

- `LiveScreen` now listens for `AppState` changes.
- When returning active with a selected Live, it refreshes Live state and chat.
- When returning active from discovery, it refreshes the Live list.

Improved playback fallback behavior:

- Native playback failures now set a `playbackFailed` state.
- Failed native playback moves the viewer into the visible fallback path instead of repeatedly trying the same failed source.
- The fallback offers the existing PulseSoc web Live viewer.

Improved host/profile navigation:

- Host profile navigation now checks for a usable profile key before navigating.
- Empty profile keys are no longer sent to the native Profile route.

Preserved local leave honesty:

- No backend viewer-leave endpoint was found in the existing production codebase.
- Native Leave remains local viewer state only.
- Server-authoritative viewer state continues to come from existing Live join/state refresh endpoints.

## Coverage Against Mission Checklist

| Area | Status | Notes |
| --- | --- | --- |
| live list refresh | Hardened locally | Active app refresh and pull-to-refresh wired. |
| scheduled/live-now states | Static verified | Scheduled section fills only when existing API returns scheduled payloads. |
| viewer join/leave state | Partially verified | Join uses backend; leave is local because no backend leave endpoint was found. |
| playback shell behavior | Hardened locally | Playback error now falls through to web fallback path. |
| playback fallback behavior | Static verified | Unsupported/failed playback uses existing web viewer fallback. |
| live chat read/send | Static verified | Existing chat API reused. |
| live reactions | Static verified | Existing reaction API reused. |
| viewer count refresh | Static verified | Existing state API refreshes viewer count. |
| host/profile navigation | Hardened locally | Empty profile keys are blocked. |
| deep-link routing | Static verified | Live routes are native; Studio remains web fallback. |
| notification tap routing | Static verified | Uses native notification routing resolver. |
| foreground/background recovery | Hardened locally | AppState refresh added. |
| loading/empty/error/offline states | Static verified | Existing states preserved. |

## Verification

Passed:

- `npm run typecheck`
- `venv/bin/python scripts/pulsesoc_native_live_audit.py`

The full final verification suite for this checkpoint is:

- `npm ci --no-audit --no-fund --progress=false`
- `npm run typecheck`
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_live_device_qa_audit.py`
- `git diff --check`
- `git status --short`

## Next Recommendation

Recommended next native feature: Native Premium + Entitlements Foundation.

Why this comes next:

- Live viewer has reached the point where real device QA is required before hosting/calls.
- Premium/entitlement state already exists server-side and is reused by Profile, themes, badges, creator readiness, and billing surfaces.
- Native Profile, Settings, Notifications, Marketplace, Feed, and Creator-adjacent surfaces need a native premium/status layer for parity.
- Premium can preserve safe web/provider checkout and billing portal fallback without rebuilding payment logic.

Reusable existing PulseSoc logic:

- `GET /api/premium/status`
- `POST /api/premium/checkout`
- `POST /api/premium/billing-portal`
- `GET /api/dashboard/economy/state`
- `premium_entitlement_service`
- `premium_capability_engine`
- `premium_identity_engine`
- `pulse_premium_profiles`
- `pulse_subscriptions`
- `pulse_premium_entitlements`
- Stripe checkout/billing portal flows
- Profile themes, premium badges, founder status, and entitlement-aware feature flags

What must be rebuilt natively:

- Premium/entitlements screen.
- Subscription/plan status display.
- Founder/Premium badge and profile-theme surfacing.
- Entitlement-aware locked/active/unavailable cards.
- Billing/checkout safe web/provider handoff.
- Settings/Profile entry points.
- Loading, error, offline, and provider-not-configured states.

Risk: Medium.

Complexity: Medium.

Safest implementation plan:

1. Inspect premium/status and dashboard economy payloads.
2. Build read-only native Premium status first.
3. Add entitlement cards using server-provided status only.
4. Use existing web/provider checkout and billing portal handoff.
5. Wire Settings/Profile entry points.
6. Add a focused audit that verifies no Stripe/payment logic is duplicated in native.
