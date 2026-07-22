# PulseSoc Native App Store Release Checklist - 2026-07-21

## Release Gate

Current decision: **NO-GO**.

Do not export, upload, or submit the current archive.

## Checklist

| Gate | Status | Evidence / Required Next Step |
| --- | --- | --- |
| Existing App Store app identified | PASS | Public app id `6777591572`, `PulseSoc`, seller `ROODY CHERIE`. |
| Existing App Store bundle id verified | PASS | Live bundle id is `com.pulsesoc.app`. |
| Native Release bundle id matches live app | FAIL | Native builds `com.pulsesoc.nativeapp`. Owner/App Store Connect decision required. |
| Apple Team inspected | PARTIAL | Xcode team is `87ZC69AGSR`; App Store Connect ownership not verified. |
| Version/build plan ready | FAIL | Expo version `0.1.0`; Xcode version `1.0` build `1`; live version `1.0`. |
| Production API configuration | PASS_WITH_REVIEW | Native config points at `https://pulsesoc.com`; local QA proxy repaired previously. |
| Development QA auto-login disabled for production | PASS_BY_SCRIPT | Physical install script unsets QA auto-login variables for device sidecar. |
| App Store signing identity installed | FAIL | Only Apple Development identities are available. |
| App Store provisioning profile available | FAIL | No App Store profile/export options found. |
| Production push entitlement | FAIL | Archive entitlement is `aps-environment=development`. |
| `get-task-allow=false` in archive | FAIL | Archive entitlement has `get-task-allow=true`. |
| Dev-client/dev-menu absent from archive | FAIL | Archive includes Expo dev-menu/dev-client artifacts. |
| Privacy manifest release-complete | FAIL | Collected data types are empty; production data flows require review. |
| Security review | PARTIAL | No secrets/profiles/archives committed by this mission; full external secret scan not run. |
| Native WebView replacement gate | FAIL | 54 hard web-exit/fallback blockers remain. |
| Notifications route parity | PASS_CODE_PATH | Route audit passes; production APNs/locked/killed app push remain physical/provider QA. |
| Deep links | PARTIAL | Native routing exists; web exits and legal/support fallbacks remain blockers. |
| IAP/subscriptions/payments | FAIL | Native paid digital access/IAP readiness and App Review metadata not proven. |
| Live/audio/calls code audits | PASS_CODE_PATH | Live, calls, radio, music focused audits passed. |
| Live/audio/calls physical QA | FAIL | Physical two-client Live/call/audio matrix not observed in this attempt. |
| Typecheck | PASS | `npm run --prefix mobile-native typecheck`. |
| Jest | PASS | 37 suites, 355 tests. |
| Expo Doctor | PASS | 17/17 checks. |
| Xcode Release archive | PASS_NOT_DISTRIBUTABLE | Archive succeeded but is development-signed. |
| IPA export | NOT_ATTEMPTED | Blocked by signing/profile/bundle/replacement gate. |
| App Store validation | NOT_ATTEMPTED | Blocked by non-distributable archive and target mismatch. |
| App Store upload | NOT_ATTEMPTED | Intentionally stopped. |
| Simulator launch | PASS | iPhone 17 Pro Max launched `com.pulsesoc.nativeapp`; screenshot captured. |
| Physical iPhone update | PASS_DEV_SIDECAR | iPhone 16 Pro installed/launched `com.pulsesoc.nativeapp.dev`. |
| Physical production replacement proof | FAIL | Production-signed bundle not installed or validated. |
| Working tree clean for release | FAIL | Pre-existing unrelated dirty release-sensitive files remain. |

## Required Before Next Upload Attempt

1. Verify App Store Connect ownership of the existing PulseSoc app and confirm whether native must ship under `com.pulsesoc.app`.
2. Create a production-clean App Store release target/profile with no Expo dev-client/dev-menu artifacts.
3. Install or provide access to the correct Apple Distribution identity and App Store provisioning profile.
4. Resolve production push entitlement and `get-task-allow=false`.
5. Complete native WebView replacement blockers or obtain explicit product/legal exceptions.
6. Reconcile App Privacy labels and `PrivacyInfo.xcprivacy`.
7. Complete IAP/subscription/App Review metadata readiness.
8. Run physical-device release QA for camera, microphone, Live, calls, Bluetooth, push, background behavior, and killed-app routing.
9. Clean or intentionally land unrelated dirty release-sensitive work.

## Current Safe Action

Continue blocker elimination and Apple release preparation. Do not upload the current native archive.
