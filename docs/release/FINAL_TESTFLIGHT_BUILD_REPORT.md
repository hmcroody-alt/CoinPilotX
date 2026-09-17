# Final TestFlight build report — 2026-09-17

Build **28** of PulseSoc `1.0.2` is uploaded to App Store Connect.

## Identity

| | |
|---|---|
| EAS build | `01d0bc35-8c61-4853-966a-3d212e0d975c` |
| Status | FINISHED |
| Profile | `production`, `distribution: store` |
| Version / build | `1.0.2` / `28` |
| Git commit | `cef0b9005bd5` |
| Bundle id | `com.pulsesoc.app` |
| Team | `87ZC69AGSR` (ROODY CHERIE, Individual) |
| Submission | `ac8668bc-cbd3-4a79-a68a-aeb5dd62e299` |
| ASC app | `6777591572` |

EAS recorded `gitCommitHash = cef0b9005bd5`, which is the same commit Railway
reports for every production service. Build 27 carries no commit hash at all, so
28 is the first store build whose provenance is checkable rather than asserted.

## The bump that would not have shipped

Raising `ios.buildNumber` in `app.json` from `27` to `28` was not enough, and the
protection suite is what caught it.

This project sets `INFOPLIST_FILE` and does **not** set
`GENERATE_INFOPLIST_FILE`, so the checked-in `mobile-native/ios/PulseSoc/Info.plist`
is authoritative and Expo's `app.json` value is ignored. EAS says so out loud
during the build:

> Specified value for `ios.bundleIdentifier` … is ignored because an ios
> directory was detected in the project.

`CFBundleVersion` still read `27`. The upload would have been rejected by App
Store Connect as a duplicate of build 27 — after a 21-minute archive upload and a
full cloud build.

`tests/protection/test_ios_build_version_contract.py` failed on exactly this,
with an error message that named the file to edit and warned against
`expo prebuild`. Three files now move together:

| File | Key | 27 → 28 |
|---|---|---|
| `mobile-native/app.json` | `ios.buildNumber` | what Expo tooling reads |
| `mobile-native/ios/PulseSoc/Info.plist` | `CFBundleVersion` | **what actually ships** |
| `mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj` | `CURRENT_PROJECT_VERSION` (×2) | what Xcode's UI shows |

This is worth recording because the failure would have been invisible locally:
`npm run verify` is green, per-file pytest is green, and the build itself
succeeds. Only the upload fails, and only at the very end.

## Gates re-run on the build-28 lineage

| Gate | Result |
|---|---|
| `realtime_audio_change_gate.py --base 7dfeb7ac --head HEAD` | **accepted**, exit 0 |
| `scripts/protection/run_protection_suite.py` | **687 checks / 46 suites**, all passed |
| `tests/protection/test_ios_build_version_contract.py` | 8 passed |

`app.json` is a `dependency_watch` protected path, so the gate rejected the bump
until a declaration addendum naming it was committed *after* the change. The
addendum is in `reports/realtime_audio_change_declaration.md` under "TestFlight
build addendum (2026-09-17)".

No `categories[].paths` entry changed. The only code-bearing content in this
range is a build number, which is read at runtime solely by
`src/screens/settings/AboutSettingsScreen.tsx` for display.

## All three targets on the same build

| Target | Build | Evidence |
|---|---|---|
| TestFlight | 28 | EAS `01d0bc35`, commit `cef0b9005bd5` |
| P3r7or (iPhone 16 Pro) | 28 | bundle UUID `B5F66474-5CD1-4EA2-B5B1-3C196453D5D6`, running PID 28535 from that exact UUID |
| iPhone 17 Pro Max (simulator) | 28 | 25 frameworks re-signed and verified, keychain entitlements embedded, PID 90884 |

Both local binaries export **86** `PulseAppleTranslation` symbols. That marker
*inverts* between lineages — a build from any commit before `6ba609b9` exports
zero, because the pod was absent from the Pods project — so its presence cannot
be explained by a stale artifact.

The simulator screenshot shows the app signed in against production: feed
loaded, Pulse Network "Connected", unread badge populated.

## What is not verified here

Apple's processing of the binary is asynchronous. At the time of writing the
upload has succeeded and Apple has begun processing; the build appearing as
available to testers in TestFlight is Apple's step, not ours.

Physical audible validation of a live call was not performed — see
`FINAL_DEVICE_INSTALL_REPORT.md` for the reasoning and for what was checked
instead. No protected audio source file changed in this range.
