# Mobile Release Update After Home Live/Status Fix

Date: 2026-06-09

## Change Summary

This update fixes the logged-out welcome/auth experience, Home status media fill, status-muted autoplay, and the disruptive Live Now block on the website. Since the iOS and Android apps are WebView shells, both platforms inherit these web fixes once the app loads the updated site.

## Mobile App Change

- Default WebView start URL changed to `https://pulsesoc.com/login?next=/pulse`.
- Logged-out users now land on the permanent premium PulseSoc welcome/login/signup screen.
- Logged-in users are redirected by the website to `/pulse`.
- Package and bundle remain `com.pulsesoc.app`.

## Build Tracking

- Commit built: `23e52d457be3952345fc76af6e93f415417af455`
- iOS EAS build: `f5e215cf-db45-4963-bd92-2a4f790b5b33`
- iOS build number: `24`
- iOS status at report update: `FINISHED`; submitted to App Store Connect and processing at Apple.
- iOS artifact: `https://expo.dev/artifacts/eas/wn37Wf7kShLrRWSqwF4yQK.ipa`
- Local iOS artifact: `/tmp/pulsesoc-ios/pulsesoc-ios-build24-home-status-live.ipa`
- EAS iOS submission: `5c8f8e08-7677-4676-a77d-5827b857715c`
- Android EAS build: `842e860f-ce36-4b67-a99f-c088efe8f247`
- Android versionCode: `21`
- Android status at report update: `FINISHED`
- Android artifact: `https://expo.dev/artifacts/eas/hjGr55ZtvTHc789S5x6JEc.aab`
- Local Android artifact: `/tmp/pulsesoc-play/pulsesoc-android-v21-home-status-live.aab`
- Android Play upload status: automatic EAS submit blocked because `./credentials/google-play-service-account.json` is not present. Manual Play Console upload can use the local AAB path above.

## Validation Plan

- Python compile
- JavaScript/TypeScript parse
- feed/status/live audits
- site functional audit where available
- mobile `npm run typecheck`
- mobile `npm run audit`
- `npx expo-doctor`
- `npx expo config`
- iOS build
- Android build or documented quota blocker

## Store Notes

Do not submit a new iOS or Android binary until the build IDs/artifacts below are filled from successful post-fix builds.

## Post-Fix Build IDs

- iOS build: `f5e215cf-db45-4963-bd92-2a4f790b5b33` (`0.1.1`, build `24`) submitted to App Store Connect.
- Android AAB: `842e860f-ce36-4b67-a99f-c088efe8f247` (`0.1.1`, versionCode `21`) ready for Google Play internal testing upload.
- Android APK: not requested in this pass; production Play artifact is AAB.
- Artifact URLs: recorded above.

## Manual Android Upload Step

Use Google Play Console → PulseSoc → Testing → Internal testing → Create/edit release, then upload:

`/tmp/pulsesoc-play/pulsesoc-android-v21-home-status-live.aab`

Release notes:

`PulseSoc Android internal QA build with permanent premium logged-out welcome/auth screen, fixed Home status rail media, muted status autoplay, removed disruptive Live block, and live feed-post flow.`
