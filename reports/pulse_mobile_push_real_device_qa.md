# PulseSoc Mobile Push Real Device QA

Date: 2026-06-07

## Goal

Verify the native notification chain:

1. PulseSoc Mobile asks permission for notifications.
2. The device receives a push token.
3. PulseSoc sends the token to the Railway backend.
4. Railway stores the token for the logged-in user.
5. Backend sends a test push.
6. Real device rings, vibrates, and shows the notification.

## Verified In Code And Local QA

- Notification permission request is present through Expo Notifications.
- Physical-device guard is present; simulators do not attempt push registration.
- Expo push token capture is present.
- Token subscription posts to `/api/push/subscribe`.
- Logout cleanup posts to `/api/push/unsubscribe`.
- Notification tap routing opens the deep link URL.
- Foreground notifications can show alerts, play sound, and set badge.
- Android notification channel uses high importance, default sound, and vibration.
- Backend stores native Expo tokens in `push_subscriptions` and `pulse_notification_devices`.
- Backend native push delivery now routes Expo tokens through the Expo push service.
- Native push payload includes high priority, default sound, default channel, and deep-link data.

## Railway Credential Readiness

Already verified in `reports/push_credentials_readiness_report.md`:

- APNs variables are loaded.
- FCM variables are loaded.
- `APNS_BUNDLE_ID` equals `com.pulsesoc.app`.
- APNs private key parsing works.
- Firebase Admin initializes safely.
- No `.p8` private key file is committed.

## Real Device Status

Final live-device notification delivery has not been completed from this workstation because it requires a physical iPhone or Android device running the PulseSoc mobile build, signed into a real user account.

Once the build is installed on a device:

1. Log in to PulseSoc Mobile.
2. Accept notification permission.
3. Confirm the app remains signed in.
4. Trigger `/api/push/test` from the logged-in session or a QA-only admin route.
5. Confirm the device shows the notification, plays default sound, vibrates, and opens the notification deep link.

## Validation Run

- Mobile TypeScript typecheck: pass.
- Mobile foundation audit: pass.
- Mobile authentication audit: pass.
- Mobile feed audit: pass.
- Mobile notifications audit: pass.
- Mobile Firebase audit: pass.
- Store submission readiness audit: pass.
- Native push delivery audit with mocked Expo provider: pass.
- Web/browser push audit with native Expo guardrails: pass.
- Notification delivery audit: pass.

## Remaining Live QA Blocker

Install a signed internal iOS or Android build on a real device. EAS is not logged in locally yet, so an iOS production/internal build could not be started from this workstation during this pass.
