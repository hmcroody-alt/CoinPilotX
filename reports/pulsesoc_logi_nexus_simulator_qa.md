# PulseSoc LogiNexus Simulator QA Foundation

Date: 2026-07-10

## Scope

Phase 2 requires the Xcode iPhone Simulator to be a trusted visual QA target before the shared global navigation and wider LogiNexus transformation continue.

This pass repairs the simulator authentication workflow only. It does not add Home features, redesign screens, change production auth, or touch WebView routes.

## Implemented

- Extended the existing native QA simulator auth helper with an explicit auto-login mode.
- Auto-login is enabled only when all of these are true:
  - the native app is running in `__DEV__`
  - `EXPO_PUBLIC_PULSE_API_BASE_URL` points to localhost or `127.0.0.1`
  - `EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN=1`
- Reused the existing `/api/mobile/auth/register` endpoint and native session-cookie persistence.
- Generated a runtime-only local QA account with a runtime-only password.
- Did not write credentials to source, reports, logs, or committed files.
- Added Login screen handling for QA auth URLs so initial/deferred simulator links are less timing-sensitive.
- Added QA-addressable login selectors for future simulator automation.

## Security Boundary

The simulator bootstrap is unavailable in production builds and unavailable against non-local API bases. It does not weaken production authentication and does not bypass backend account/session creation.

## Simulator Workflow

Recommended local command:

```bash
EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5107 \
EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN=1 \
npm run --prefix mobile-native start:qa -- --host localhost --clear
```

Then launch the installed simulator dev build:

```bash
xcrun simctl openurl <DEVICE_UDID> 'pulsesoc://expo-development-client/?url=http%3A%2F%2F127.0.0.1%3A8081'
```

## Verified Result

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` passed.
- `npm run --prefix mobile-native typecheck` passed.
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` passed 17/17 checks.
- `venv/bin/python scripts/pulsesoc_native_simulator_qa_auth_audit.py` passed.
- Xcode iPhone 17 Pro Simulator launched the installed `com.pulsesoc.nativeapp` dev build against the local QA API.
- The explicit QA auto-login created an authenticated runtime-only local QA account and reached the signed-in native app.
- The authenticated session routed to Home through `pulsesoc:///pulse`.

Simulator evidence:

- `reports/screenshots/logi-nexus-phase2-simulator-auth-home.png`
- `reports/screenshots/logi-nexus-phase2-simulator-auth-home-routed.png`

## Remaining

- Continue Phase 2 with the Global Navigation LogiNexus Foundation now that authenticated simulator Home is reachable.
- Development builds still show the existing Expo warning overlay; this is a separate media dependency/runtime cleanup task and did not block authenticated navigation proof.
