# PulseSoc Native Camera Studio Device QA + Hardening

Date: 2026-07-04

## Scope

This mission focused only on Native Camera Studio QA and hardening. LiveKit calls, native hosting, co-hosting, and unrelated feature work were intentionally deferred.

Production WebView camera routes were not modified. The native app remains a parallel client for the existing PulseSoc backend and reuses the existing camera, media upload, preview, post, reel, status, profile, Messenger, storage, Mux/R2, validation, and moderation behavior.

## Current Verification State

| Area | Browser QA | iOS Simulator | Physical iPhone | Android Emulator | Physical Android | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Camera route | Auth-gate verified | Not verified | Not verified | Not verified | Not verified | External device tooling blocked |
| Camera permission denied/allowed | Not applicable | Not verified | Not verified | Not verified | Not verified | Requires simulator/device |
| Microphone permission denied/allowed | Not applicable | Not verified | Not verified | Not verified | Not verified | Requires simulator/device |
| Photo capture | Not applicable | Not verified | Not verified | Not verified | Not verified | Requires simulator/device |
| Video capture | Not applicable | Not verified | Not verified | Not verified | Not verified | Requires simulator/device |
| Front/back camera switch | Not applicable | Not verified | Not verified | Not verified | Not verified | Requires simulator/device |
| Gallery fallback | Not verified this pass | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Preview screen | Not verified this pass | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Caption/privacy/destination flow | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Compression metadata | Static verified | Not verified | Not verified | Not verified | Not verified | Requires upload/device evidence |
| Upload progress | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Upload cancel/retry | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Publish to Feed | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Publish to Status | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Publish to Reels | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Profile avatar/cover handoff | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Messenger attachment handoff | Static verified | Not verified | Not verified | Not verified | Not verified | Requires authenticated QA and device |
| Large image/video behavior | Not verified | Not verified | Not verified | Not verified | Not verified | Requires device and network QA |
| Background interruption recovery | Not verified | Not verified | Not verified | Not verified | Not verified | Requires device QA |

## What Was Verified In This Pass

- `mobile-native/app.json` declares the parallel QA identity `com.pulsesoc.nativeapp`.
- iOS camera, microphone, and photo-library permission strings are present.
- Android camera, audio, media read, and notification permissions are present.
- `expo-camera` and `expo-image-picker` are configured in Expo plugins.
- `CameraStudioScreen` uses `CameraView`, `useCameraPermissions`, and `useMicrophonePermissions`.
- Camera Studio exposes photo/video controls, front/back switch, microphone toggle, gallery fallback, preview metadata, caption/privacy/destination controls, and safe web fallback for advanced effects.
- Shared upload code carries `compression_policy` and `destination` metadata to the existing backend upload route.
- Existing backend routes remain authoritative for upload, preview, post, reel, status, profile, and Messenger publishing.
- iPhone 17 Pro iOS Simulator boot was verified after full Xcode became available.
- Expo Go installed on the iPhone 17 Pro simulator and loaded the PulseSoc Native bundle to the login screen behind Expo Go's first-run developer menu.

## Device Tooling Findings

Local machine state observed during this mission:

- `xcode-select -p` points to `/Library/Developer/CommandLineTools`.
- `/usr/bin/xcrun` exists.
- `xcrun simctl list devices available` fails because `simctl` is not available from the active developer directory.
- `adb` was installed through Homebrew `android-platform-tools`.
- `adb` is available at `/opt/homebrew/bin/adb`.
- `adb version` reports Android Debug Bridge `1.0.41`, version `37.0.0-14910828`.
- `adb devices` starts the daemon successfully but shows no attached or authorized devices.
- `idevice_id` is not available in `PATH`.
- `ios-deploy` is not available in `PATH`.
- No iPhone, iPad, Android, Pixel, Samsung, Motorola, or OnePlus device was visible through USB system profiling.
- No `GoogleService-Info.plist`, `google-services.json`, or entitlements file was found under `mobile-native/`.

Later iOS Simulator update:

- `xcode-select -p` now returns `/Applications/Xcode.app/Contents/Developer`.
- iPhone 17 Pro simulator is available at UDID `7B3BEEBC-6135-497D-91CD-A3E70C927D56`.
- Runtime is iOS 26.5.
- `xcrun simctl bootstatus 7B3BEEBC-6135-497D-91CD-A3E70C927D56 -b` completed.
- Expo Doctor now reports a toolchain mismatch: Expo SDK 51 is not compatible with Xcode 26.6.0; required Xcode is `<=16.2.0`.
- Simulator QA reached PulseSoc login behind Expo Go's first-run developer menu, but Camera Studio interaction was not verified.

These are machine/setup blockers, not PulseSoc code blockers.

## Blockers

Priority order:

1. Resolve the Expo SDK 51 and Xcode 26.6 compatibility mismatch, either by using an SDK 51-compatible Xcode path or by planning an Expo SDK/dev-client upgrade path.
2. Run Camera Studio simulator QA through an installed `com.pulsesoc.nativeapp` development build instead of Expo Go.
3. Attach and trust at least one physical iPhone for real camera/microphone/photo-library QA.
4. Attach and authorize at least one physical Android device or start an Android emulator so `adb devices` lists a target.
5. Install Android Studio/emulator images if emulator QA is needed.
6. Configure provider credentials and entitlements before push/deep-link/lock-screen camera handoff claims.
7. Create or reuse QA-safe authenticated PulseSoc credentials for publish-to-Feed/Status/Reels/Profile/Messenger flows.
8. Run device builds for `com.pulsesoc.nativeapp`; do not use the production `com.pulsesoc.app` identity.

## Hardening Decisions

- No native code blocker was patched in this pass because no simulator or physical device was available to reproduce camera behavior.
- No production WebView camera route was touched.
- No backend business logic was duplicated.
- No provider credential or production app identity was changed.
- Advanced AR, Banuba-native effects, Marketplace media creation, and advanced editor behavior remain on safe fallback.
- Camera/mic/compression/video/upload behavior remains release-blocked until device QA is completed.

## Required Device QA Checklist

Coverage terms: camera permission denied/allowed; microphone permission denied/allowed; photo capture; video capture; front/back camera switch; gallery fallback; compression metadata; upload cancel/retry; publish to Feed; publish to Status; publish to Reels; Profile avatar/cover; Messenger attachment; background interruption recovery.

Run this checklist on at least one iPhone and one Android device:

1. Start a development build for `com.pulsesoc.nativeapp`.
2. Sign in with QA-safe credentials.
3. Open Camera Studio from Home Feed.
4. Test camera permission denied/allowed states and verify the denial state plus gallery fallback.
5. Test microphone permission denied/allowed states and verify muted recording state for denied audio.
6. Allow camera permission and verify the camera preview renders.
7. Switch front/back cameras repeatedly.
8. Capture a photo and verify preview/retake/publish.
9. Switch to video, allow microphone permission, and record a short video.
10. Select a large image from gallery and verify validation/upload behavior.
11. Select a large video from gallery and verify validation/upload behavior.
12. Publish to Feed and verify native Post Detail opens.
13. Publish to Status and verify native Status Detail/rail updates.
14. Publish to Reels and verify native Reel Detail opens.
15. Update profile avatar and cover from Camera Studio.
16. Open Camera Studio from Messenger and send a media attachment.
17. Cancel upload mid-flight and verify retry works.
18. Background the app during capture/upload and verify recovery.
19. Confirm backend delivery, moderation, processing, and media status logs.
20. Confirm no production WebView behavior changed.

## Exact Commands

Static verification:

```bash
npm ci --prefix mobile-native --no-audit --no-fund --progress=false
npm run --prefix mobile-native typecheck
cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose
venv/bin/python scripts/pulsesoc_native_camera_studio_device_qa_audit.py
git diff --check
```

iOS simulator setup after full Xcode is selected:

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
xcrun simctl list devices available
npm run --prefix mobile-native ios:simulator
```

Android setup after platform tools are installed:

```bash
adb devices
npm run --prefix mobile-native android
```

Development builds:

```bash
npm run --prefix mobile-native build:ios:development
npm run --prefix mobile-native build:android:development
```

## Next Recommendation

The next highest-value action is to unblock and execute real-device Camera Studio QA for `com.pulsesoc.nativeapp`.

Do not move to Native LiveKit calls yet. LiveKit calls depend on the same camera/microphone/device-permission surface plus push/ringing/background behavior, so proceeding before Camera Studio device QA would compound unknowns.
