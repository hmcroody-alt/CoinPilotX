# PulseSoc EAS iOS Build Blocker Report

Date: 2026-06-07

## Original Failure

The iOS EAS build failed before Apple credential entry after `expo-updates` was installed automatically. The failure was:

```text
Cannot read properties of undefined (reading 'policy')
```

EAS also warned that `ios.googleServicesFile` was not checked in and would not be uploaded.

## Root Cause

- The production EAS build profile used `channel: "production"` even though OTA updates were not intentionally configured.
- EAS attempted to configure `expo-updates`, adding `expo-updates`, `runtimeVersion`, and `updates.url`.
- That left the project in a mixed OTA state and triggered the `policy` crash.
- Firebase config files are intentionally ignored and uncommitted, so cloud builds need EAS file variables or a build-time materialization hook.

## Fix Applied

- Removed `expo-updates` from `package.json` and `package-lock.json`.
- Removed `runtimeVersion` and `updates.url` from `app.json`.
- Removed `channel` from the `preview` and `production` build profiles in `eas.json`.
- Preserved `extra.eas.projectId`: `712c1e38-a984-433f-bce1-f517693bd3fb`.
- Added `ITSAppUsesNonExemptEncryption: false` to iOS `infoPlist`.
- Added `app.config.js` dynamic config support for EAS Firebase file variables.
- Added `scripts/prepare-firebase-config.js` and `eas-build-pre-install` to materialize Firebase config files during EAS builds without committing them.
- Added audit coverage for the Firebase prep hook and update-channel guardrail.

## Firebase File Handling

The Firebase app config files remain local and ignored:

- `credentials/firebase/google-services.json`
- `credentials/firebase/GoogleService-Info.plist`

The following EAS production file variables exist and are masked by EAS:

- `EAS_GOOGLE_SERVICES_JSON`
- `EAS_GOOGLE_SERVICE_INFO_PLIST`
- `GOOGLE_SERVICES_JSON`
- `GOOGLE_SERVICE_INFO_PLIST`

No Firebase file contents were printed or committed.

## Verification

- `npx expo-doctor`: pass, 17/17 checks.
- `npx expo config --type public`: resolves `PulseSoc`, `com.pulsesoc.app`, EAS project ID, Firebase references, and iOS encryption declaration.
- `npm run typecheck`: pass.
- `npm run audit`: pass.
- `scripts/pulse_store_submission_readiness_audit.py`: pass.
- `scripts/pulse_native_push_delivery_audit.py`: pass.
- `git diff --check`: pass.

## EAS iOS Build Check

Command run safely without hidden Apple credential entry:

```bash
npx eas-cli build --platform ios --profile production --non-interactive --no-wait --message "PulseSoc real device push QA"
```

Result:

- The previous `policy` crash did not recur.
- The `expo-updates` install/configuration prompt did not recur.
- EAS reached iOS credential setup.
- Build stopped because Apple signing credentials are not configured in non-interactive mode.

Current expected blocker:

```text
Credentials are not set up. Run this command again in interactive mode.
```

## Remaining Action

Run the EAS iOS build command locally and enter Apple credentials:

```bash
cd /Users/hmcherie/Desktop/CoinPilotX/mobile/pulse-react-native
npx eas-cli build --platform ios --profile production --no-wait --message "PulseSoc real device push QA"
```

When EAS prompts for Apple ID, password, or 2FA, the account owner must enter them directly in the visible terminal. Do not paste Apple credentials into chat.

## Status

The EAS iOS build configuration blocker is fixed. The remaining iOS blocker is Apple Developer credential setup for signing.
