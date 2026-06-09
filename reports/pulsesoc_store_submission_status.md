# PulseSoc Store Submission Status

Date: 2026-06-09

## iOS App Store

- App: PulseSoc
- Bundle ID: `com.pulsesoc.app`
- App Store Connect app ID: `6777591572`
- Current submitted build: `0.1.1 (24)`
- EAS build ID: `f5e215cf-db45-4963-bd92-2a4f790b5b33`
- Source commit: `23e52d457be3952345fc76af6e93f415417af455`
- IPA: `https://expo.dev/artifacts/eas/wn37Wf7kShLrRWSqwF4yQK.ipa`
- Local IPA: `/tmp/pulsesoc-ios/pulsesoc-ios-build24-home-status-live.ipa`
- EAS submission ID: `5c8f8e08-7677-4676-a77d-5827b857715c`
- Submission result: uploaded successfully to App Store Connect.
- Apple status: processing after upload. Apple usually takes several minutes before the build appears in App Store Connect/TestFlight.

## Android Google Play

- App: PulseSoc
- Package: `com.pulsesoc.app`
- Track target: Internal Testing
- Current AAB: `0.1.1`, versionCode `21`
- EAS build ID: `842e860f-ce36-4b67-a99f-c088efe8f247`
- Source commit: `23e52d457be3952345fc76af6e93f415417af455`
- AAB: `https://expo.dev/artifacts/eas/hjGr55ZtvTHc789S5x6JEc.aab`
- Local AAB: `/tmp/pulsesoc-play/pulsesoc-android-v21-home-status-live.aab`
- EAS Submit status: blocked because `mobile/pulse-react-native/credentials/google-play-service-account.json` is not present.

## Manual Android Upload Instructions

Use Google Play Console -> PulseSoc -> Testing -> Internal testing -> create/edit release.

Upload:

`/tmp/pulsesoc-play/pulsesoc-android-v21-home-status-live.aab`

Confirm:

- Package: `com.pulsesoc.app`
- VersionCode: `21`

Release notes:

`PulseSoc Android internal QA build with permanent premium logged-out welcome/auth screen, fixed Home status rail media, muted status autoplay, removed disruptive Live block, live feed-post flow, WebView mirror behavior, native push/share bridge, and media permissions.`

Keep Android on Internal Testing unless production rollout is explicitly approved.
