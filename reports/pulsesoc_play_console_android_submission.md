# PulseSoc Play Console Android Submission

Date: 2026-06-09

## Current Android Build

- App: PulseSoc
- Package: `com.pulsesoc.app`
- Build profile: production/store
- App version: `0.1.1`
- VersionCode: `21`
- EAS build ID: `842e860f-ce36-4b67-a99f-c088efe8f247`
- Source commit: `23e52d457be3952345fc76af6e93f415417af455`
- Build status: `FINISHED`
- AAB URL: `https://expo.dev/artifacts/eas/hjGr55ZtvTHc789S5x6JEc.aab`
- Local AAB: `/tmp/pulsesoc-play/pulsesoc-android-v21-home-status-live.aab`

This supersedes all older Android bundles, including versionCode `16`, `18`, `19`, and `20`.

## Included Fixes

- Android opens the live PulseSoc website through the WebView shell.
- Logged-out users land on the permanent premium PulseSoc login/signup welcome experience.
- Status rail media fills story cards with no black empty space.
- Status videos autoplay muted and only unmute through user action.
- The large middle Home `Live Now` block is removed.
- Active live sessions continue to surface as LIVE feed posts.
- Native push/share bridge and media permissions remain in place.

## Upload Status

Automatic EAS Submit was attempted and failed because the Google Play service account key file is not available at:

`mobile/pulse-react-native/credentials/google-play-service-account.json`

That file is a secret and should not be committed.

Manual upload is ready with:

`/tmp/pulsesoc-play/pulsesoc-android-v21-home-status-live.aab`

## Internal Testing Release Notes

`PulseSoc Android internal QA build with permanent premium logged-out welcome/auth screen, fixed Home status rail media, muted status autoplay, removed disruptive Live block, live feed-post flow, WebView mirror behavior, native push/share bridge, and media permissions.`

## Tester List

Confirm `hmcroody@gmail.com` remains included in the Internal Testing tester list.

## Opt-In Link

Pending manual Play Console upload of versionCode `21`.

## Do Not Use

Do not upload older AABs:

- `/tmp/pulsesoc-play/pulsesoc-v16.aab`
- `/tmp/pulsesoc-play/pulsesoc-android-v16.aab`
- `/tmp/pulsesoc-play/pulsesoc-android-v18-webview-mirror.aab`
