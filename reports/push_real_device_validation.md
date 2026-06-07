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

Firebase mobile config files remain uncommitted. To support cloud builds without committing those files, the following EAS production secret file variables were created:

- `GOOGLE_SERVICES_JSON`
- `GOOGLE_SERVICE_INFO_PLIST`

No file contents or secret values were printed.

## Build Attempts

### Android

- Build command started successfully.
- EAS build ID: `1dfd8b56-ae3f-4bef-bae7-13c48c1c973f`
- Status at last check: `IN_PROGRESS`
- Build profile: `production`
- Distribution: store
- Version code: `3`
- Logs: `https://expo.dev/accounts/hmcroody/projects/pulsesoc/builds/1dfd8b56-ae3f-4bef-bae7-13c48c1c973f`

### iOS

- iOS build setup reached Apple credential validation.
- EAS requested Apple Developer login to generate and validate signing credentials.
- Apple credential prompt was not completed during this pass.
- iOS TestFlight build was not created yet.

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

1. Complete Apple Developer credential setup in EAS for iOS signing.
2. Wait for Android EAS build to finish, then install it on a real Android device.
3. Create an iOS TestFlight build, then install it on a real iPhone.
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

Resume iOS build with:

```bash
npx eas-cli build --platform ios --profile production --no-wait --message "PulseSoc real device push QA"
```

When EAS asks for Apple ID, password, or 2FA, the account owner must enter those directly. After the iOS and Android builds are installed on real devices, run the live notification test matrix above.
