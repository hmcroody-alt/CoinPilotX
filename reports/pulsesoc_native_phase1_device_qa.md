# PulseSoc Native Phase 1 Device QA

Date: 2026-07-04

## Result

Phase 1 install/start/typecheck/backend readiness is verified. Real-device or simulator UI QA is not verified from this workstation because the native device tooling is unavailable:

- iOS simulator: `xcrun simctl` failed with `unable to find utility "simctl", not a developer tool or in PATH`.
- Android device/simulator: `adb` is not installed.

No major native features were added. No production WebView/mobile shell paths were changed by this QA pass.

## Reuse-First Migration Rule

PulseSoc is already built. The native app must be a new client for the existing PulseSoc platform, not a duplicated platform.

Before building each native feature:

1. Inspect the existing web/backend implementation.
2. Reuse the existing route, service, payload shape, permissions, validation, moderation, premium checks, notification rules, media pipeline, database behavior, and external-provider flow wherever possible.
3. Port only safe reusable frontend logic into React Native TypeScript, such as API wrappers, validation, pagination, retry logic, upload state machines, date/time formatting, feed/message sync, and error handling.
4. Do not copy DOM, HTML, CSS, or browser-only code directly into React Native.
5. Rebuild only the native UI/device layer: screens, navigation, gestures, camera, microphone, image/video picker, push behavior, lock-screen calls, native video player, deep links, and share sheet.

## Verified Checks

| Check | Result | Evidence |
| --- | --- | --- |
| `npm ci --no-audit --no-fund --progress=false` | Pass | Lockfile installed 1218 packages. Warnings were dependency peer/deprecation warnings, not install failures. |
| `npm run typecheck` | Pass | TypeScript completed with no errors. |
| `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` | Pass | `17/17 checks passed. No issues detected!` |
| Expo public config | Pass | `extra.pulseApiBaseUrl` resolves to `https://pulsesoc.com`; app ID is `com.pulsesoc.nativeapp`. |
| Metro start | Pass | `npx expo start --localhost --port 8099` reached `Waiting on http://localhost:8099`; server was stopped after readiness. |
| Production health | Pass | `https://pulsesoc.com/health` returned `{"ok":true,"service":"coinpilotx-web"}` with HTTP 200. |
| Logged-out session safety | Pass | `https://pulsesoc.com/api/mobile/auth/session` returned `{"authenticated":false,"ok":true,"user":null}` with HTTP 200. |
| Foundation audit | Pass | `venv/bin/python scripts/pulsesoc_native_app_foundation_audit.py` passes. |
| Dependency graph audit | Pass | `venv/bin/python scripts/pulsesoc_native_dependency_graph_audit.py` passes. |
| WebView production path | Pass | No scoped diff under `mobile/` or PulseShell bridge files during this QA pass. |

## Phase 1 Device QA Matrix

| Test | Status | Notes |
| --- | --- | --- |
| App opens | Not verified on device/simulator | Metro starts, but no simulator/device bridge exists in this shell. |
| API base URL loads correctly | Verified without device | Expo config and backend health confirm `https://pulsesoc.com`. |
| Login screen works | Not verified on device/simulator | Screen typechecks; real UI interaction requires simulator/device. |
| Signup screen works | Not verified on device/simulator | Screen typechecks; real UI interaction requires simulator/device and safe test account plan. |
| Session restore after close/reopen | Not verified on device/simulator | Session API safely returns logged-out state; secure-store persistence requires device/simulator. |
| Logout works | Not verified on device/simulator | Logout code typechecks and clears secure cookie; real session lifecycle requires device/simulator. |
| Denied push permission does not break app | Static verified, device not verified | `registerPushDevice()` returns `{ ok:false }` for denied permission and catches token/backend failures. |
| Accepted push permission registers safely | Not verified on device/simulator | Code posts Expo token to `/api/push/subscribe`; real token requires physical device or supported push environment. |
| Mission Control loads | Not verified on device/simulator | Route is wired to `/api/dashboard/mission-control`; authenticated runtime requires device/session. |
| Messenger list loads | Not verified on device/simulator | Route is wired to `/api/pulse/messages/conversations`; authenticated runtime requires device/session. |
| Basic chat send works | Not verified on device/simulator | Route is wired to `/api/pulse/messages/<conversation_id>/send`; safe authenticated test conversation required. |
| Pulse AI chat works | Not verified on device/simulator | Route is wired to `/api/pulse/assistant/chat`; authenticated runtime/provider state requires device/session. |
| Profile loads | Not verified on device/simulator | Route is wired to `/api/pulse/profile/me`; authenticated runtime requires device/session. |
| Settings loads | Not verified on device/simulator | Screen typechecks; push behavior requires device/simulator. |

## Existing Platform Reuse Inventory For Phase 1

| Native surface | Existing source to reuse | Backend/API contract | Native-only rebuild |
| --- | --- | --- | --- |
| Auth/session | Existing mobile auth routes in `bot.py`; secure session behavior from previous mobile client | `/api/mobile/auth/session`, `/login`, `/register`, `/logout`; server session cookie remains authoritative | Native forms, secure-store cookie persistence, navigation state |
| Mission Control | Existing dashboard mission-control route and server-side account logic | `/api/dashboard/mission-control`; no duplicated ranking or account logic | Native screen layout and loading/empty states |
| Messenger | Existing message routes and Communications V2 migration map | `/api/pulse/messages/*` first; inspect Communications V2 before expanding | Native list/detail/composer, gestures, keyboard behavior |
| Basic chat send | Existing conversation membership, permissions, and message persistence | `/api/pulse/messages/<conversation_id>/send` | Native composer and optimistic UI only after server behavior is confirmed |
| Pulse AI | Existing server-side AI router/provider logic | `/api/pulse/assistant/chat`; providers stay server-side | Native prompt/reply screen and retry states |
| Profile | Existing profile read/update/media rules | `/api/pulse/profile/me` and profile media routes | Native profile UI and media picker later |
| Push | Existing push registration and notification tables/rules | `/api/push/subscribe`, notification preferences, device-token handling | Native permission prompt, token fetch, deep links |

## Blockers

1. Real-device/simulator UI QA is blocked on this workstation.
   - Missing iOS simulator tooling: `simctl`.
   - Missing Android bridge: `adb`.
2. Authenticated Phase 1 flows were not exercised because no device/simulator was available and no test credentials were used from this shell.
3. Push accepted-permission registration was not exercised because Expo push token generation requires a supported device environment.

## Next QA Step

Run the same app on either:

- a local machine with Xcode command-line tools and an iOS simulator, or
- an Android emulator/device with `adb`, or
- a physical Expo Go/dev-build device.

Use a safe PulseSoc test account, then record screenshots/logs for login, signup, session restore, logout, denied push, accepted push, Mission Control, Messenger, chat send, Pulse AI, Profile, and Settings.
