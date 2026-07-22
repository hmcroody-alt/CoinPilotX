# PulseSoc Native App Store Release Attempt - 2026-07-21

## Decision

**NO-GO for App Store upload and WebView replacement.**

The native app can build a local iOS Release archive, and the physical iPhone sidecar app was updated, installed, and launched. The archive is not App Store-submittable because it is development-signed, uses the native sidecar bundle identifier, carries development push entitlements, and the strict native WebView-replacement audit still fails on 54 hard web-exit/fallback blockers.

No IPA was exported. No App Store validation/upload was attempted.

## Repository And Branch

- Repository: `/Users/hmcherie/Desktop/CoinPilotX`
- Branch: `release/undx-nexus-core-v4`
- Starting HEAD: `6608f934 docs(native): record NRB-059 auth cluster as committed (f43da843)`
- Pre-existing dirty work preserved:
  - `mobile-native/app.json`
  - `mobile-native/ios/PulseSocNative.xcodeproj/project.pbxproj`
  - `mobile-native/src/assets/brand/pulsesoc-logo-mark.png`
  - `mobile-native/assets/`
  - `reports/pulsesoc_native_live_progress.md`
  - `reports/security/security_findings_ledger_a0cc15cd.md`
  - `scripts/pulsesoc_native_live_audit.py`

## Existing App Store Target

Apple public lookup for app id `6777591572` returned:

- Track name: `PulseSoc`
- Seller/artist: `ROODY CHERIE`
- Live bundle id: `com.pulsesoc.app`
- Live version: `1.0`
- Minimum iOS: `15.1`
- Current version release date: `2026-07-01T07:00:00Z`
- URL: `https://apps.apple.com/us/app/pulsesoc/id6777591572`

Native Release configuration currently builds:

- App name: `PulseSoc`
- Expo slug: `pulsesoc-native`
- Native iOS bundle id: `com.pulsesoc.nativeapp`
- Xcode Release bundle id: `com.pulsesoc.nativeapp`
- Xcode Release marketing version: `1.0`
- Xcode Release build: `1`
- Expo app version: `0.1.0`
- Development team: `87ZC69AGSR`

## Release-Blocking Differences

1. The live App Store app uses `com.pulsesoc.app`; the native Release build uses `com.pulsesoc.nativeapp`.
2. Only Apple Development signing identities are installed locally; no Apple Distribution identity is available.
3. The Release archive is signed by `Apple Development: ROODY CHERIE (HB5FV6P922)`.
4. The archive entitlements include `aps-environment=development` and `get-task-allow=true`.
5. The archive uses the `iOS Team Provisioning Profile: com.pulsesoc.nativeapp` development profile.
6. The archive includes Expo dev-menu/dev-client build artifacts.
7. No App Store provisioning profile, export options, or App Store Connect upload credentials were available.
8. The strict native WebView replacement audit fails with 54 hard web-exit/fallback blockers.
9. Physical-device feature QA remains incomplete for production push, camera, microphone, Live/audio/calls, Bluetooth, background execution, and killed-app notification routing.
10. App Store privacy metadata is not release-complete because the local privacy manifest declares no collected data types despite production auth, messaging, media, notification, and commerce surfaces.

## Validation Commands

| Check | Result | Evidence |
| --- | --- | --- |
| `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` | PASS | Dependency install completed with warnings only. |
| `npm run --prefix mobile-native typecheck` | PASS | TypeScript completed. |
| `npm test --prefix mobile-native -- --runInBand --silent` | PASS | 37 suites, 355 tests. |
| `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` | PASS | 17/17 checks passed. |
| `scripts/pulsesoc_native_app_foundation_audit.py` | PASS | Foundation audit passed. |
| `scripts/pulsesoc_native_feature_parity_audit.py` | PASS | Feature parity audit passed. |
| `scripts/pulsesoc_native_live_audit.py` | PASS | Live audit passed. |
| `scripts/pulsesoc_native_mission_standard_audit.py` | PASS | Mission standard audit passed. |
| `scripts/pulsesoc_native_global_navigation_audit.py` | PASS | Navigation audit passed. |
| `scripts/pulsesoc_native_notification_route_parity_audit.py` | PASS | Notification routing audit passed. |
| `scripts/pulsesoc_native_persistent_radio_home_reselect_audit.py` | PASS | Radio/Home reselect audit passed. |
| `scripts/pulsesoc_native_live_webrtc_guest_audio_repair_audit.py` | PASS | Live WebRTC repair audit passed. |
| `scripts/pulsesoc_native_calls_audit.py` | PASS | Calls audit passed. |
| `scripts/pulsesoc_native_music_upload_audit.py` | PASS | Music upload audit passed. |
| `scripts/pulsesoc_native_undx_chat_conversation_audit.py` | PASS | UNDX route and conversation audit passed. |
| `scripts/pulse_store_submission_readiness_audit.py` | PASS | Existing store audit passed. |
| `scripts/apple_review_compliance_audit.py` | PASS | Existing Apple review audit passed. |
| `scripts/pulse_app_store_review_fix_audit.py` | PASS | Existing App Review repair audit passed. |
| `scripts/app_store_review_repair_audit.py` | PASS | Existing App Review repair audit passed. |
| `scripts/pulsesoc_native_webview_replacement_audit.py` | FAIL | 54 hard web-exit/fallback blockers. |

## Archive Attempt

Command:

```sh
cd mobile-native && xcodebuild archive \
  -workspace ios/PulseSocNative.xcworkspace \
  -scheme PulseSocNative \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath /tmp/pulsesoc-native-release-attempt-2026-07-21/PulseSocNative.xcarchive
```

Result: **ARCHIVE SUCCEEDED, NOT DISTRIBUTABLE**.

Archive inspection:

- Archive path: `/tmp/pulsesoc-native-release-attempt-2026-07-21/PulseSocNative.xcarchive`
- Log path: `/tmp/pulsesoc-native-release-attempt-2026-07-21/archive.log`
- Bundle id: `com.pulsesoc.nativeapp`
- Signing identity: `Apple Development: ROODY CHERIE (HB5FV6P922)`
- Provisioning profile: `iOS Team Provisioning Profile: com.pulsesoc.nativeapp`
- Entitlements: `aps-environment=development`, `get-task-allow=true`
- Dev menu: Expo dev-menu artifacts present in the archive build.

## Simulator Validation

- Simulator device: iPhone 17 Pro Max, iOS 26.5.
- Native bundle launched: `com.pulsesoc.nativeapp`.
- Screenshot evidence: `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-app-store-release-attempt-2026-07-21/simulator-launch.png`
- Classification: **Simulator launch verified**.

## Physical iPhone Validation

- Device: `P3r7or`, iPhone 16 Pro (`iPhone17,1`)
- Device identifier: `F45E640F-6D02-514E-877C-B764E8D6818F`
- Installed guarded sidecar bundle: `com.pulsesoc.nativeapp.dev`
- Install script: `scripts/install_pulsesoc_native_dev_iphone.sh`
- Install log: `/tmp/pulsesoc-native-release-attempt-2026-07-21/physical-install.log`
- Result: **Build/install/launch verified for the dev sidecar app only.**

This does not prove production App Store signing, production APNs, real push delivery, camera, microphone, Bluetooth audio routing, lock-screen calls, background audio, killed-app routing, or real multi-device Live/call behavior.

## Upload Attempt

Upload was intentionally not attempted.

Reasons:

- Native Release bundle id does not match the live App Store bundle id.
- Archive is development-signed.
- Production/App Store entitlements are not present.
- No Apple Distribution identity is installed.
- No App Store export profile/options are available.
- WebView replacement gate still fails.
- App Store Connect listing/build train/IAP metadata could not be verified.

## Final Judgment

PulseSoc Native is not ready for App Store upload or WebView replacement today.

The highest-value next action is to clear the remaining native-only web-exit blockers while Apple-side release credentials, bundle ownership, production provisioning, privacy metadata, and App Store Connect access are verified by the owner.
