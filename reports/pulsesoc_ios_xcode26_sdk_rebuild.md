# PulseSoc iOS Xcode 26 SDK Rebuild

Date: 2026-06-07

## Rejection

Apple rejected the previous PulseSoc IPA with:

`ITMS-90725`

Reason: the uploaded app was built with the iOS 17.5 SDK. Apple now requires iOS/iPadOS apps to be built with the iOS 26 SDK or later, included in Xcode 26 or later.

## Original Mobile App

- App name: PulseSoc
- iOS bundle identifier: `com.pulsesoc.app`
- Expo SDK: 51
- EAS project: `@hmcroody/pulsesoc`
- EAS project ID: `712c1e38-a984-433f-bce1-f517693bd3fb`

Identifiers were not changed.

## Fix Applied

The first fix attempted to pin the iOS production EAS build profile to an Xcode 26 image:

`macos-sequoia-15.6-xcode-26.2`

That confirmed SDK 51 was too old for the Xcode 26 toolchain. The build failed with a native compile error:

`cannot find 'TARGET_OS_SIMULATOR' in scope`

The app was then upgraded to Expo SDK 55 and the Xcode 26 image pin was kept.

Current mobile app:

- Expo SDK: 55
- React Native: 0.83.6
- React: 19.2.0
- iOS build image: `macos-sequoia-15.6-xcode-26.2`

## Validation

Completed after the SDK 55 upgrade:

- `npm run typecheck`: passed
- `npm run audit`: passed
- `npx expo-doctor`: passed
- `npx expo install --check`: passed
- `npx expo config --type public`: confirmed app name and bundle identifier remain unchanged
- `npx expo export --platform ios`: passed
- `git diff --check`: passed

## Build

Failed SDK 51 Xcode 26 build:

- EAS build ID: `ba2bb80f-a51a-41e1-b3b1-197684e91d77`
- Build number: 7
- Status: failed
- Message: `PulseSoc iOS Xcode 26 SDK rebuild`

Current SDK 55 Xcode 26 build:

- EAS build ID: `89a65d68-096f-4f07-8d1d-ccb6bdfebfe7`
- Build number: 9
- Status: failed
- Message: `PulseSoc Expo 55 Xcode 26 SDK rebuild retry`
- Failure: JavaScript bundling failed because `babel-preset-expo` was missing from top-level dependencies.

Current SDK 55 Xcode 26 build after bundler fix:

- EAS build ID: `809587da-a66a-48b8-b408-bee56e07e05a`
- Build number: 10
- Status: failed
- Message: `PulseSoc SDK 55 Xcode 26 SDK IPA`
- Failure: `expo-av` failed native compilation against Expo Modules Core under the Xcode 26 toolchain.

Final SDK 55 Xcode 26 build after replacing `expo-av` with `expo-video`:

- EAS build ID: `2f3012d6-30fa-4d4f-b747-679434b5badc`
- Build number: 11
- Status: finished
- Message: `PulseSoc SDK 55 Xcode 26 final IPA`
- IPA artifact: `https://expo.dev/artifacts/eas/6PXRGUiF2SBxnkSrJQG2sm.ipa`

Confirmed EAS build toolchain evidence:

- VM image: `macos-sequoia-15.6-xcode-26.2`
- Xcode: `26.2 (17C52)`
- iOS SDK evidence: `iPhoneOS26.2.sdk`

## App Store Connect

Build number 11 was submitted to App Store Connect with the EAS production submit profile.

Result:

- Upload accepted by App Store Connect.
- Apple processing started after upload.
- App Store Connect TestFlight page now shows `Build 11`.
- Build 11 status: `Ready to Submit`.
- TestFlight page: `https://appstoreconnect.apple.com/apps/6777591572/testflight/ios`

## Bundler Fix

Local iOS export failed after the SDK 55 upgrade because Hermes could not compile private fields when the wrong Babel preset version was present.

Resolution:

- Added `babel-preset-expo` explicitly.
- Pinned `babel-preset-expo` to the SDK 55-compatible version.
- Re-ran local iOS export successfully.
- Re-ran Expo Doctor successfully.

## Native Video Compatibility Fix

The SDK 55 / Xcode 26 build exposed an `expo-av` native compatibility failure. The mobile feed video component was migrated to the current Expo video package.

Resolution:

- Removed `expo-av`.
- Added `expo-video`.
- Registered the `expo-video` config plugin.
- Updated feed media playback to use `useVideoPlayer` and `VideoView`.

## Final Status

The PulseSoc iOS app was rebuilt with the iOS 26 SDK via Xcode 26 and uploaded successfully. Apple accepted the binary upload, TestFlight processing completed, and build 11 is visible in App Store Connect with status `Ready to Submit`.

## Notes

The upload attempt for build number 8 failed before build creation because of a transient EAS upload network `EPIPE` error. The retry succeeded and created build number 9.
