# PulseSoc Native TestFlight Upload - 2026-07-21

## Final Decision

**BLOCKED.**

No TestFlight build was uploaded. No IPA was exported. No public App Store release was attempted.

The repository-side native replacement gate is currently strong enough to continue release preparation: the strict native WebView replacement audit passes with `0` hard web-exit blockers and `26/26` critical surfaces covered. The current blocker is now the App Store/TestFlight packaging chain: App Store Connect access, production bundle identity, distribution signing, production APNs entitlements, production-clean packaging, version/build selection, and physical TestFlight smoke testing.

## Starting State

- Repository: `/Users/hmcherie/Desktop/CoinPilotX`
- Branch: `release/undx-nexus-core-v4`
- Starting SHA: `995810bd4e26024b4a902a637906549db88a7668`
- Current mission target: `mobile-native`
- Existing production App Store app: `PulseSoc`
- Apple app ID: `6777591572`
- Public live bundle ID: `com.pulsesoc.app`
- Public live version: `1.0`
- Public release date: `2026-07-01T07:00:00Z`

Public lookup source used for non-authenticated app identity only:

```text
https://itunes.apple.com/lookup?id=6777591572
```

Authenticated App Store Connect ownership, provider, uploaded build history, agreements, IAP state, and Internal TestFlight groups were **not verified** because no App Store Connect API key, app-specific password, or authenticated upload credentials are configured in this environment.

## Dirty File Disposition

| Path | Disposition | Notes |
| --- | --- | --- |
| `mobile-native/app.json` | `EXTERNAL_REVIEW_REQUIRED` | Changes display name to `PulseSoc` and adds `./assets/icon.png`; still uses `ios.bundleIdentifier=com.pulsesoc.nativeapp`, not the live `com.pulsesoc.app`. |
| `mobile-native/ios/PulseSocNative.xcodeproj/project.pbxproj` | `EXTERNAL_REVIEW_REQUIRED` | Changes display names to `PulseSoc Dev` / `PulseSoc`; Release target still uses `PRODUCT_BUNDLE_IDENTIFIER=com.pulsesoc.nativeapp` and `CODE_SIGN_IDENTITY=Apple Development`. |
| `mobile-native/src/assets/brand/pulsesoc-logo-mark.png` | `EXTERNAL_REVIEW_REQUIRED` | Deleted in working tree while `mobile-native/assets/icon.png` exists; likely part of branding/icon move but not safe to stage blindly. |
| `mobile-native/assets/icon.png` | `EXTERNAL_REVIEW_REQUIRED` | New icon referenced by `app.json`; not reviewed as a production App Store asset set. |
| `reports/security/security_findings_ledger_a0cc15cd.md` | `INTENTIONAL_UNRELATED_WORK` | Security ledger update predates this mission and was preserved. |

No certificates, provisioning profiles, `.p8` keys, archives, IPAs, or App Store credentials were staged or committed.

## Native Release Configuration Observed

| Field | Current value |
| --- | --- |
| Expo project | `@hmcroody/pulsesoc-native` |
| Expo project ID | `03be39d7-db88-43af-af5f-50c267d830f8` |
| Expo app version | `0.1.0` |
| Xcode workspace | `mobile-native/ios/PulseSocNative.xcworkspace` |
| Xcode scheme | `PulseSocNative` |
| Xcode configuration | `Release` |
| Xcode version | `26.6 (17F113)` |
| iPhoneOS SDK | `26.5` |
| Release bundle ID | `com.pulsesoc.nativeapp` |
| Expected production bundle ID | `com.pulsesoc.app` |
| Marketing version | `1.0` |
| Build number | `1` |
| Development team | `87ZC69AGSR` |
| Release code sign identity | `Apple Development` |
| Code sign style | `Automatic` |
| Entitlements file | `mobile-native/ios/PulseSocNative/PulseSocNative.entitlements` |
| APNs entitlement in source | `aps-environment=development` |

## Signing And App Store Connect Findings

`security find-identity -v -p codesigning` found only Apple Development identities:

```text
Apple Development: ROODY CHERIE (HB5FV6P922)
Apple Development: ROODY CHERIE (HB5FV6P922)
```

No `Apple Distribution` signing identity was available.

No App Store Connect API key file was found in:

- `~/.appstoreconnect/private_keys`
- `~/.private_keys`
- `~/private_keys`

No App Store Connect credential environment variable names were present. EAS is authenticated as:

```text
hmcroody
hmcroody@gmail.com
```

That does not prove access to the existing PulseSoc App Store Connect record, the correct Apple provider, or the existing production bundle ID.

## Upload Stop Point

Archive/export/upload were intentionally not attempted in this current mission.

Reasons:

1. The Release bundle ID is `com.pulsesoc.nativeapp`; the live App Store app uses `com.pulsesoc.app`.
2. Release signing is configured as `Apple Development`.
3. No Apple Distribution identity is installed.
4. The source entitlement is still `aps-environment=development`.
5. The production build number cannot be selected safely without authenticated App Store Connect build-train history.
6. No App Store Connect API key, provider, or upload session is configured.
7. `expo-dev-client` remains in the native dependency graph; a production-clean App Store target/profile still needs hardening.
8. Privacy metadata is not release-complete: `PrivacyInfo.xcprivacy` declares an empty collected-data list while the product has auth, messaging, media upload, notifications, and commerce-adjacent surfaces.

Uploading from this state would either fail validation or risk targeting the wrong app identity.

## Validation Run

| Check | Result |
| --- | --- |
| `git status --short` | PASS, dirty work identified and preserved |
| `git branch --show-current` | PASS, `release/undx-nexus-core-v4` |
| `git rev-parse HEAD` | PASS, `995810bd4e26024b4a902a637906549db88a7668` |
| `git diff --check` | PASS |
| `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` | PASS, warnings only |
| `npm run --prefix mobile-native typecheck` | PASS |
| `npm test --prefix mobile-native -- --runInBand --silent` | PASS, `38` suites / `373` tests |
| `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` | PASS, `17/17` checks |
| `python3 scripts/pulsesoc_native_webview_replacement_audit.py` | PASS, `0` hard blockers / `26/26` critical surfaces |
| `python3 scripts/pulse_store_submission_readiness_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_app_foundation_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_feature_parity_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_mission_standard_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_global_navigation_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_notification_route_parity_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_persistent_radio_home_reselect_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_live_webrtc_guest_audio_repair_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_calls_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_music_upload_audit.py` | PASS |
| `python3 scripts/pulsesoc_native_undx_chat_conversation_audit.py` | PASS |
| `python3 scripts/apple_review_compliance_audit.py` | PASS |
| `python3 scripts/pulse_app_store_review_fix_audit.py` | PASS |
| `python3 scripts/app_store_review_repair_audit.py` | PASS |

`venv/bin/python` is not present in this checkout, so Python audits were run with `/opt/homebrew/bin/python3` (`Python 3.14.0`).

## Simulator And Device Status

Simulator availability:

- `PulseSoc Compact iPhone`
- `PulseSoc iPhone 16 Pro`
- `PulseSoc iPhone 16 Pro Max`
- `iPhone 17`
- `iPhone 17 Pro`
- `iPhone 17 Pro Max`

Physical device detected by Xcode:

- `P3r7or`, iPhone, device id `00008140-000E2D9A2EE8801C`

No TestFlight build was available to install on the physical iPhone, so TestFlight smoke testing was **NOT OBSERVED**.

## Required External Actions

1. Provide authenticated App Store Connect access for the existing PulseSoc record `6777591572`.
2. Confirm the correct App Store Connect provider/team and Apple Developer Team for the existing production app.
3. Confirm native must ship as an update under `com.pulsesoc.app`.
4. Install/provide a valid Apple Distribution certificate for the correct team.
5. Create/download an App Store provisioning profile for `com.pulsesoc.app` with production APNs and required capabilities.
6. Provide or configure App Store Connect API key/app-specific password for upload.
7. Confirm current highest uploaded build number so the next build can be incremented correctly.
8. Confirm App Store privacy/IAP/export-compliance state.

## Required Repository Actions After External Verification

1. Create a production-clean release target/profile that uses `com.pulsesoc.app` while preserving the `com.pulsesoc.nativeapp.dev` sidecar workflow.
2. Set production entitlements (`aps-environment=production`, `get-task-allow=false` through App Store provisioning).
3. Remove Expo dev-client/dev-menu artifacts from the App Store archive.
4. Synchronize Expo version/build with Xcode `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION`.
5. Reconcile `PrivacyInfo.xcprivacy` and App Store privacy labels.
6. Archive, inspect codesign/entitlements, export IPA, validate, upload, wait for processing, and assign to Internal TestFlight.
7. Install the processed TestFlight build on a physical iPhone and run the smoke test.

## TESTFLIGHT_READY

**NO.**

## BLOCKED Classification

- `EXTERNAL_ACTION_REQUIRED - APP STORE CONNECT ACCESS`
- `EXTERNAL_ACTION_REQUIRED - DISTRIBUTION SIGNING`
- `EXTERNAL_ACTION_REQUIRED - APP STORE PROVISIONING`
- `EXTERNAL_ACTION_REQUIRED - VERSION/BUILD HISTORY`
- `REPOSITORY_ACTION_REQUIRED - PRODUCTION CLEAN RELEASE TARGET`
- `REPOSITORY_ACTION_REQUIRED - PRIVACY METADATA RECONCILIATION`
