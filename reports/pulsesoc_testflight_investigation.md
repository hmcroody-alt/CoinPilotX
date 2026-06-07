# PulseSoc TestFlight Investigation

Date: 2026-06-07

## Current Finding

PulseSoc is not ready in TestFlight yet. Do not treat TestFlight as ready until PulseSoc appears inside the TestFlight app on the iPhone.

## Verification Results

1. App Store Connect access: verified.
2. PulseSoc app exists in App Store Connect: verified.
   - App Store Connect app ID: `6777591572`
   - App name shown: `PulseSoc`
3. EAS IPA exists: verified.
   - EAS iOS build ID: `17ea56bf-1702-456b-9545-ff8b385c2f9f`
   - EAS iOS build status: `FINISHED`
   - IPA artifact: `https://expo.dev/artifacts/eas/3XhtDVFabjgusarohxhXut.ipa`
4. IPA uploaded to App Store Connect: not verified; currently appears not uploaded.
5. App Store Connect -> PulseSoc -> TestFlight build list: verified empty.
   - Page says: `No Builds`
   - Page says: `Submit a build to start testing.`
6. Build processing status: not applicable yet because no build appears in App Store Connect.
7. Internal tester assignment: not configured for PulseSoc.
   - TestFlight -> All Testers shows `Testers (0)`.
8. Internal Testing groups: not configured.
   - TestFlight sidebar shows no Internal Testing group.
9. Invitation email: expected missing because there is no uploaded/assigned build and no tester group.

## Failing Point

The failing point is App Store Connect submission/upload, followed by TestFlight tester setup.

The IPA was built successfully by EAS, but it has not appeared in App Store Connect TestFlight. EAS Submit could not upload it in non-interactive mode because an App Store Connect API key is not configured.

Command attempted:

```bash
npx eas-cli submit --platform ios --profile production --id 17ea56bf-1702-456b-9545-ff8b385c2f9f --non-interactive --wait --what-to-test "PulseSoc real-device push QA build"
```

Result:

```text
App Store Connect API Keys cannot be set up in --non-interactive mode.
```

Interactive submit was started and reached Apple Developer login/API key setup. It was stopped intentionally because Apple credentials must be entered only by the account owner in a visible terminal.

## Required Next Step

Run this in the visible Mac Terminal:

```bash
cd /Users/hmcherie/Desktop/CoinPilotX/mobile/pulse-react-native
npx eas-cli submit --platform ios --profile production --id 17ea56bf-1702-456b-9545-ff8b385c2f9f --wait --what-to-test "PulseSoc real-device push QA build"
```

When EAS asks to generate an App Store Connect API key, choose yes. When it asks for Apple ID, password, or 2FA, enter those directly in the visible terminal.

## After Upload Completes

1. Open App Store Connect -> PulseSoc -> TestFlight.
2. Wait for build processing to finish.
3. Create an Internal Testing group if none exists.
4. Add the Apple ID as an internal tester.
5. Assign the processed build to the internal testing group.
6. Confirm PulseSoc appears in the TestFlight app.

## Status

Not complete. PulseSoc does not appear in TestFlight yet.
