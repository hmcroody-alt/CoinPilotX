# PulseSoc Play Console Android Submission

Date: 2026-06-07

## Current Status

The PulseSoc Android app record has been created in Google Play Console.

- Play Console app name: PulseSoc
- Google Play app ID: `4972265125306629074`
- Package name: `com.pulsesoc.app`
- Track opened: Internal testing
- Published internal testing release: `5 (0.1.0)`
- Target SDK: 35
- Release status: available to internal testers
- Tester list selected: `PulseSoc Internal Testers`
- Tester list includes: account-owner supplied tester email
- Internal test join URL: `https://play.google.com/apps/internaltest/4701754927570662217`
- First AAB upload accepted, but Google Play blocked publish because it targeted API level 34.
- Replacement Android production build with target SDK 35 was uploaded and published to internal testing.

## Verified Build Artifact

The Android production build is ready for submission:

- Package name: `com.pulsesoc.app`
- App name: PulseSoc
- EAS Android build status: finished
- Artifact type: AAB
- Version code: 3
- Temporary local AAB path for manual upload: `/tmp/pulsesoc-play/pulsesoc-v3.aab`

## Replacement Build

Google Play requires Android apps to target at least API level 35. The first uploaded bundle was version code 3 and target SDK 34, so it cannot be saved or published.

The mobile Expo config was updated to use `expo-build-properties` with:

- Android compile SDK: 35
- Android target SDK: 35

First replacement EAS Android build:

- Build ID: `5a2758f8-1999-42fb-bebb-b9152627549f`
- Version code: 4
- Status: failed
- Failure: `expo-modules-core` Kotlin compile issue under API 35 nullable permission metadata.

Second replacement EAS Android build:

- Build ID: `cac196bc-0373-41c9-bdb0-fc47f257c530`
- Version code: 5
- Status: finished
- Artifact URL: `https://expo.dev/artifacts/eas/r3a9j723c7N4NVCM9A3BT7.aab`
- Temporary local AAB path: `/tmp/pulsesoc-play/pulsesoc-v5-api35.aab`
- Fix included: build-time patch for `expo-modules-core` API 35 nullable permission check.

## Completed

- Google Play contact phone verification completed by the account owner.
- App name entered as PulseSoc.
- Package name entered as `com.pulsesoc.app`.
- Package availability confirmed.
- App type selected as App.
- Pricing selected as Free.
- Required app creation declarations accepted after account-owner approval.
- PulseSoc app dashboard created.
- Internal testing track opened.
- Internal testing release draft created.

## Next Steps After Verification

1. Open the internal test join URL with the tester Google account.
2. Accept the internal test.
3. Install PulseSoc from Google Play when the listing becomes available to the tester.
4. Verify app launch, login, push permission prompt, and notification token registration on a real Android device.
5. Complete store listing basics.
6. Configure app content declarations.
7. Continue closed testing readiness, because Google requires closed testing before production access.

## Resolved Blocker

The uploaded version code 3 AAB targeted API level 34. Google Play required target API level 35, so version code 3 could not be published.

Resolution:

- Updated mobile Android build config to target API 35.
- Added a build-time patch for the Expo SDK 51 `expo-modules-core` nullable permission compile issue under API 35.
- Built version code 5.
- Uploaded version code 5 to Google Play.
- Published version code 5 to internal testing.
- Saved internal tester list selection.

## Safety Notes

- No secrets were exposed.
- No API keys or service account files were created.
- No unrelated Play Console settings were changed.
- No existing production apps were modified.
- No service account credentials were added.
