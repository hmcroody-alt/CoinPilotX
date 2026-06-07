# PulseSoc Real Device Push Validation

Date: 2026-06-07

## Completion Status

Not complete. Per mission rules, this validation must not be marked complete until at least one real iPhone and one real Android device receive live PulseSoc notifications.

## EAS Configuration

- EAS login: completed as Expo account `hmcroody`.
- EAS project: created and linked.
- EAS project full name: `@hmcroody/pulsesoc`
- EAS project ID: `712c1e38-a984-433f-bce1-f517693bd3fb`
- App display name: `PulseSoc`
- Expo slug: `pulsesoc`
- Deep link scheme: `pulse`
- iOS bundle identifier: `com.pulsesoc.app`
- Android package: `com.pulsesoc.app`
- API base URL: `https://pulsesoc.com`

## Push Configuration

- `expo-notifications` plugin is configured.
- Android `POST_NOTIFICATIONS` permission is configured.
- Android notification channel uses high importance, default sound, and vibration.
- iOS notification permission flow is implemented through Expo Notifications.
- Notification tap routing opens deep links.
- Backend APNs and FCM credentials were previously verified in Railway production.

## Firebase / EAS Secret Files

Firebase mobile config files remain uncommitted. To support cloud builds without committing those files, the following EAS production file variables were created:

- `EAS_GOOGLE_SERVICES_JSON`
- `EAS_GOOGLE_SERVICE_INFO_PLIST`
- `GOOGLE_SERVICES_JSON`
- `GOOGLE_SERVICE_INFO_PLIST`

No file contents or secret values were printed.

## Build Attempts

### Android

- Build command completed successfully.
- EAS build ID: `1dfd8b56-ae3f-4bef-bae7-13c48c1c973f`
- Status at last check: `FINISHED`
- Build profile: `production`
- Distribution: store
- Version code: `3`
- Build artifact: `https://expo.dev/artifacts/eas/4GN3JjK1MDXtdxG3rEzPsp.aab`
- Logs: `https://expo.dev/accounts/hmcroody/projects/pulsesoc/builds/1dfd8b56-ae3f-4bef-bae7-13c48c1c973f`

### iOS

- Apple Developer account validation was completed by the account owner.
- EAS iOS production build queued successfully.
- EAS build ID: `17ea56bf-1702-456b-9545-ff8b385c2f9f`
- Status at last check: `IN_PROGRESS`
- Build profile: `production`
- Distribution: store
- Build number: `6`
- Logs: `https://expo.dev/accounts/hmcroody/projects/pulsesoc/builds/17ea56bf-1702-456b-9545-ff8b385c2f9f`

## Live Notification Tests

Not executed yet. The following live tests remain pending on physical devices:

- Direct message
- Status reply
- Status reaction
- Mention
- Security alert
- Marketplace alert
- Test notification

For each test, verify:

- Notification appears.
- Sound plays.
- Vibration occurs.
- Badge updates.
- Deep link opens the correct screen.

## Current Blockers

1. Wait for iOS EAS build to finish, then upload/submit it to TestFlight if EAS does not auto-submit.
2. Install the Android build on a real Android device or submit it to Google Play internal testing.
3. Install the iOS build on a real iPhone through TestFlight.
4. Log into PulseSoc on both devices.
5. Accept notification permission on both devices.
6. Trigger live notification events and confirm delivery on both devices.

## QA Already Passed

- Mobile TypeScript typecheck.
- Mobile foundation audit.
- Mobile authentication audit.
- Mobile feed audit.
- Mobile notifications audit.
- Mobile Firebase audit.
- Store submission readiness audit.

## Next Action

Monitor iOS build with:

```bash
npx eas-cli build:list --platform ios --limit 1
```

After the iOS and Android builds are installed on real devices, run the live notification test matrix above.
