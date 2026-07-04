# PulseSoc Native Device QA Setup

Date: 2026-07-04

## Current State

The native app remains isolated under `mobile-native/` and the production WebView app remains untouched.

Repo-side QA setup completed in this checkpoint:

- Added Expo web QA dependencies:
  - `react-native-web`
  - `react-dom`
  - `@expo/metro-runtime`
- Added QA scripts:
  - `npm run start:qa`
  - `npm run web:qa`
  - `npm run ios:simulator`
  - `npm run android:emulator`
  - `npm run build:ios:development`
  - `npm run build:ios:simulator`
  - `npm run build:android:development`
  - `npm run prebuild:ios`
  - `npm run prebuild:android`
- Added `mobile-native/eas.json` development, simulator, preview, and production build profiles.
- Added optional `EXPO_PUBLIC_EXPO_PROJECT_ID` support for Expo push token registration.
- Existing Expo config already declares:
  - app scheme: `pulsesoc`
  - iOS bundle identifier: `com.pulsesoc.nativeapp`
  - Android package: `com.pulsesoc.nativeapp`
  - camera, microphone, photo library, media, and notification permission strings/permissions.

## Remaining Blockers

The repo is better prepared for QA, but real device QA is not fully unblocked on this machine yet.

Remaining blockers in priority order:

1. iOS simulator tooling is blocked because `/usr/bin/xcrun` exists but `xcrun simctl list devices available` fails with `xcrun: error: unable to find utility "simctl", not a developer tool or in PATH`.
2. Android emulator/physical device tooling is blocked because `adb` is not available in `PATH`.
3. QA browser workflow is partially unblocked by dependencies, but Metro currently fails on local macOS file permissions: `EPERM: operation not permitted` reading files under `mobile-native/node_modules`. The affected tree has `com.apple.provenance` attributes, and `xattr -dr com.apple.provenance mobile-native` is denied by macOS with `Operation not permitted`.
4. Physical iPhone testing still requires Apple developer team/signing, a device, and either Expo Go for limited smoke testing or an EAS development build for full native-module testing.
5. Physical Android testing still requires Android platform tools, USB/debugging or emulator setup, and an APK/AAB/dev-client install path.
6. Push testing still requires an EAS project ID, physical devices, notification credentials, and backend push registration validation.
7. Deep-link testing still requires installed app builds and verified `pulsesoc://` custom scheme handling; universal links require domain-side association files and app entitlements.
8. Live playback, camera, microphone, media picker, upload, and background/lock-screen behavior still require actual device/simulator testing.

## Required External Software

Install or configure:

- Node.js compatible with Expo SDK 51.
- Xcode full app, not only command-line tools.
- Xcode command-line tools selected with `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`.
- iOS simulator runtimes installed through Xcode.
- Android Studio.
- Android SDK platform tools.
- Android emulator images or a physical Android device.
- EAS CLI access through `npx eas-cli` or a globally installed `eas`.
- Expo account access.

## Required Apple Setup

Required for iOS simulator:

- Install full Xcode.
- Open Xcode once and accept required setup.
- Install iOS simulator runtime.
- Confirm:

```bash
xcrun simctl list devices available
```

Required for physical iPhone:

- Apple Developer Program membership.
- Developer team access.
- Registered iPhone UDID for internal/ad hoc profiles if not using automatic EAS credential management.
- Bundle identifier ownership for `com.pulsesoc.nativeapp`.
- Development or Ad Hoc provisioning profile.
- Development certificate or EAS managed credentials.
- APNs key/certificate for push testing.

## Required Android Setup

Required for emulator/physical Android:

- Install Android Studio.
- Install Android SDK Platform Tools.
- Install Android Emulator and at least one API image.
- Export `ANDROID_HOME` or `ANDROID_SDK_ROOT`.
- Add platform tools to `PATH`.
- Confirm:

```bash
adb devices
```

Required for physical Android:

- Enable Developer Options.
- Enable USB debugging.
- Trust the development machine.
- Confirm the device appears in `adb devices`.
- Configure FCM credentials for real push testing through Expo/EAS.

## Required Expo Setup

Required:

- Log in to Expo:

```bash
npx eas-cli login
```

- Link or create an EAS project:

```bash
cd mobile-native
npx eas-cli init
```

- Record the generated EAS project ID and expose it to native builds either through EAS config or:

```bash
export EXPO_PUBLIC_EXPO_PROJECT_ID=<eas-project-id>
```

Expo Go:

- Useful for limited smoke testing when native modules are compatible.
- Not sufficient for complete QA because LiveKit/native call work and production-grade push testing require development builds or standalone builds.

Development build:

- Required for complete native-device QA.
- Use the `development` and `development-simulator` profiles in `mobile-native/eas.json`.

## Required Push Configuration

Push testing requires:

- Physical device for Expo push token registration.
- EAS project ID available to `expo-notifications`.
- Expo push token returned by `Notifications.getExpoPushTokenAsync`.
- Backend `/api/push/subscribe` reachable at `EXPO_PUBLIC_PULSE_API_BASE_URL` or the default `https://pulsesoc.com`.
- iOS APNs credentials configured in EAS/Apple.
- Android FCM credentials configured in EAS/Firebase.
- Notification permission accepted on device.
- Device token visible in backend push subscription records.

Repo-side improvement:

- `mobile-native/src/api/push.ts` now passes `projectId` to `getExpoPushTokenAsync` when `EXPO_PUBLIC_EXPO_PROJECT_ID`, EAS config, or Expo extra config provides one.

## Required Certificates

iOS:

- Apple Development certificate for simulator/device development builds.
- Apple Distribution certificate for internal/TestFlight/app-store builds.
- APNs key or certificate for push notifications.

Android:

- Debug keystore for local development.
- Upload key or Play signing credentials for release.
- Firebase Cloud Messaging project credentials for push notifications.

## Required Provisioning

iOS:

- Development provisioning profile for physical devices.
- Ad Hoc or internal distribution profile for non-TestFlight installs.
- App Store/TestFlight provisioning when ready.
- Associated domains entitlement only if universal links are added and verified.

Android:

- Emulator or physical device install target.
- Debug signing for local development.
- Internal distribution signing for QA builds.
- Release signing later.

## Required Environment Variables

Core:

```bash
export EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com
export EXPO_PUBLIC_EXPO_PROJECT_ID=<eas-project-id>
```

Android:

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
```

iOS, if Xcode is not selected:

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
```

CI/EAS optional:

```bash
export EXPO_TOKEN=<expo-access-token>
```

## Exact Commands To Start Testing

Install and validate:

```bash
cd mobile-native
npm ci --no-audit --no-fund --progress=false
npm run typecheck
EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose
```

Start Metro for a development build:

```bash
cd mobile-native
npm run start:qa
```

Start Expo Go/native Metro:

```bash
cd mobile-native
npm start
```

Start browser QA, after local file permissions allow Metro to read `node_modules`:

```bash
cd mobile-native
npm run web:qa
```

## Exact Commands To Build iOS

iOS simulator local run:

```bash
cd mobile-native
npm run ios:simulator
```

Generate native iOS project if needed:

```bash
cd mobile-native
npm run prebuild:ios
```

EAS iOS simulator development build:

```bash
cd mobile-native
npm run build:ios:simulator
```

EAS iOS physical-device development build:

```bash
cd mobile-native
npm run build:ios:development
```

## Exact Commands To Build Android

Android emulator/local device run:

```bash
cd mobile-native
npm run android:emulator
```

Generate native Android project if needed:

```bash
cd mobile-native
npm run prebuild:android
```

EAS Android physical-device development build:

```bash
cd mobile-native
npm run build:android:development
```

## Exact Commands To Launch QA

Limited browser QA:

```bash
cd mobile-native
npm run web:qa
```

Android QA:

```bash
adb devices
cd mobile-native
npm run android:emulator
```

iOS simulator QA:

```bash
xcrun simctl list devices available
cd mobile-native
npm run ios:simulator
```

Development-build QA:

```bash
cd mobile-native
npm run start:qa
```

Then open the installed development build on the device and connect it to the Metro server.

## Feature-Specific QA Entry Points

After the app launches, verify:

- Auth/session restore.
- Push permission deny/accept.
- Notification badge and deep links.
- Messenger list, chat send, retry, attachments.
- Feed scroll, post detail, comments, composer.
- Media upload and media viewer.
- Reels playback and gestures.
- Status viewer and creator.
- Marketplace browse/detail.
- Search/Saved/Groups.
- Live viewer playback/chat/reactions.
- Premium checkout/billing fallback.
- Creator Studio, Growth Center, Intelligence/Alerts.
- Offline/cache restore.

## Is The Native App Now Ready For Real Device QA?

Not yet on this machine.

The repository is better prepared, and the Expo web dependency gap is fixed, but actual real-device QA is still blocked by external local-machine setup:

1. Fix iOS simulator tooling by installing/selecting full Xcode so `xcrun simctl` works.
2. Install Android platform tools so `adb devices` works.
3. Resolve macOS provenance/privacy permissions on `mobile-native/node_modules` so Metro web can read package files, or run the repo from a location without those restrictions.
4. Create/link an EAS project and set `EXPO_PUBLIC_EXPO_PROJECT_ID`.
5. Configure Apple and Android push credentials.
6. Establish physical-device or simulator log capture.

Do not claim real iOS, Android, push, media, Live, or deep-link QA until those blockers are resolved and the flows are actually tested.
