# PulseSoc Store Submission Status

## iOS App Store

Status checked: 2026-06-08 evening.

- App: PulseSoc
- iOS version: `1.0`
- Attached build: `0.1.0 (20)`
- App Store Connect status: `Waiting for Review`
- Action taken: no new iOS build submitted; build 20 remains attached.
- Current review issue status: no rejection or request for information shown at the time of this check.

Do not submit another iOS build unless Apple rejects the current submission or requests a required change.

## Android Google Play

Status checked: 2026-06-09.

- App: PulseSoc
- Package: `com.pulsesoc.app`
- Track: Internal Testing
- Release draft: draft 3
- Correct AAB: pending new Android WebView mirror build
- Previous EAS build ID: `e50dad9c-28ec-4f22-b1ca-b7eb23143ae0`
- Previous app version: `0.1.0`
- Previous versionCode: `16`
- Previous build source commit: `6a7d9d0 Fix PulseSoc welcome auth and language flow`
- Upload status: blocked until the new Android WebView mirror AAB is built and manually uploaded or submitted with a Google Play service account.
- Opt-in link status: pending new Android WebView mirror upload.

The Play Console draft must not be advanced with older bundles. VersionCode `16` is now superseded because Android must open the same WebView mirror as iOS and the live website.

## Manual Android Upload Instructions

1. Open Google Play Console -> PulseSoc -> Testing -> Internal testing -> release draft 3.
2. Click `Upload` under App bundles.
3. Select the new WebView mirror AAB generated after the 2026-06-09 Android fix.
4. Confirm the uploaded bundle shows package `com.pulsesoc.app` and a versionCode higher than `16`.
5. Add release notes:

   `PulseSoc Android internal QA build with direct PulseSoc WebView mirror behavior, performance updates, native push/share bridge, media permissions, and web-aligned mobile UI.`

6. Add/confirm tester `hmcroody@gmail.com`.
7. Save and continue through preview.
8. Keep the release on Internal Testing only.
9. Copy the tester opt-in link after the release is available.

Do not roll out Android to production without explicit approval.
