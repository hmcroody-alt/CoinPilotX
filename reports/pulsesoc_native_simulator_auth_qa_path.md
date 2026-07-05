# PulseSoc Native Reliable Authenticated Simulator Input Path

Date: 2026-07-05

## Scope

This mission did not add a user-facing feature and did not move to LiveKit calls.

The goal was to unblock authenticated iOS Simulator QA for the parallel native app identity `com.pulsesoc.nativeapp` without weakening production authentication, changing production WebView routes, or bypassing server authority.

## Options Investigated

| Option | Result | Reason |
| --- | --- | --- |
| Improve `cliclick` text-entry automation | Rejected as primary path | Prior simulator evidence showed username/email text could be dropped, missed, or pasted into the wrong field. Password entry worked more reliably than the identifier field, so this remained brittle. |
| Manual Simulator typing | Kept as fallback | Safe and production-real, but not repeatable enough for automated QA evidence. |
| QA-only seeded session endpoint | Rejected for this pass | Would add backend auth surface area and would need careful server gating. Not needed because normal `/api/mobile/auth/login` already works against the local QA backend. |
| QA-only deep-link login/session bootstrap | Selected | It avoids Simulator text-entry flakiness while still calling the existing backend login API. It is gated to development native builds and localhost API base URLs only. |
| Test credentials with paste/input flow | Rejected as primary path | The prior paste attempt filled the password field while leaving username/email empty. |

## Implemented Path

Implemented a QA-only simulator deep link:

```text
pulsesoc://qa/simulator-login?identifier=<qa-user>&password=<qa-password>&redirect=/pulse/camera/photo&target=feed&mode=photo
```

The handler:

- Exists only in `mobile-native/src/session/qaSimulatorAuth.ts`.
- Is enabled only when `__DEV__` is true.
- Is disabled on web.
- Requires `EXPO_PUBLIC_PULSE_API_BASE_URL` to resolve to `127.0.0.1`, `localhost`, or `::1`.
- Calls the existing native `signIn()` flow, which calls the existing `/api/mobile/auth/login` backend API.
- Stores the normal backend session cookie through existing native session storage.
- Queues Camera Studio navigation only after auth succeeds.
- Does not add a backend QA auth endpoint.
- Does not modify production WebView routes.
- Does not weaken production auth.

## Production Safety Boundary

This path is simulator/local-QA only.

It is not enabled for:

- Production builds where `__DEV__` is false.
- Web builds.
- Remote API bases such as `https://pulsesoc.com`.
- Production app identity `com.pulsesoc.app`.
- Production WebView routes.

The backend remains authoritative because the bootstrap does not mint or inject a session. It only submits QA credentials to the existing mobile login endpoint when the native app is running against a localhost QA backend.

## Exact QA Command

After starting the local QA backend and Metro:

```bash
xcrun simctl openurl 7B3BEEBC-6135-497D-91CD-A3E70C927D56 'pulsesoc://qa/simulator-login?identifier=native_camera_sim_qa&password=<redacted>&redirect=/pulse/camera/photo&target=feed&mode=photo'
```

The password is intentionally not committed. Use a temporary local QA user only.

## Camera Studio QA Coverage

The reliable input path is designed to unblock:

- Authenticated simulator login.
- Session cookie restore against the local QA backend.
- Native Camera Studio route navigation.
- Camera config loading through `/api/pulse/camera/config`.
- Permission-denied and permission-prompt states where the simulator supports them.
- Gallery fallback and preview flow where simulator media is available.
- Caption/privacy/destination flow.
- Upload handoff where simulator file access supports it.

Physical device QA is still required for:

- Real camera capture.
- Real microphone capture.
- Front/back hardware switching.
- Large video memory and compression behavior.
- Background capture/upload interruptions.
- Push notification tap handoff into media flows.

## Current Result

Status: implemented and simulator-verified for authenticated route access.

Verified on the iPhone 17 Pro simulator, UDID `7B3BEEBC-6135-497D-91CD-A3E70C927D56`:

- Local QA backend health passed at `http://127.0.0.1:5107/health`.
- Existing mobile login API returned `authenticated: true` for the local QA user.
- Existing camera config API returned `ok: true` with `provider: native_fallback` and upload endpoint `/api/pulse/media/upload`.
- Metro bundled the native app with `EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5107`.
- QA-only simulator deep link authenticated the installed `com.pulsesoc.nativeapp` development build without text entry.
- Camera Studio opened in authenticated Feed/photo mode.
- Camera Studio opened in authenticated Reel/video mode.
- Camera config rendered inside the native Camera Studio UI with provider `native_fallback`.
- Microphone and photo-library permissions were grant/revoke tested through `xcrun simctl privacy`.
- Session restore passed after terminating and reopening the installed app; the app returned authenticated to Home.

Screenshots captured:

- `/tmp/pulsesoc-sim-auth-qa-01-dev-client.png`
- `/tmp/pulsesoc-sim-auth-qa-04-after-fresh-deeplink.png`
- `/tmp/pulsesoc-sim-auth-qa-07-reel-destination.png`
- `/tmp/pulsesoc-sim-auth-qa-08-session-restore.png`

Still not verified:

- Native camera permission prompt allow/deny through a real touch path.
- Gallery picker selection.
- Preview after selected/captured media.
- Upload progress, retry, cancel, and publish handoff.
- Real camera/microphone capture.

`xcrun simctl` does not provide tap injection in this environment, and `cliclick` did not reliably affect the Simulator app surface. The selected auth path is still valid and safer than a backend QA session endpoint because it preserves the existing auth API and uses client-only gates that cannot operate against production API hosts.

## Next Recommendation

Next highest-value action: add or use a reliable Simulator touch/media automation path, such as XCTest, Appium, Detox, or manual Simulator interaction, then complete gallery, preview, upload, retry/cancel, and publish handoff QA. After that, run physical iPhone and Android Camera Studio QA. Do not move to Native LiveKit calls until camera, microphone, gallery, upload, and publish behavior has credible simulator and device evidence.
