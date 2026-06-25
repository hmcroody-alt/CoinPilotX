# PulseSoc Push Credentials Audit

Date: 2026-06-25

## Expo

QA browser inspection of the logged-in Expo account showed:

- Expo Android and iOS credentials section is present.
- Apple distribution certificate exists.
- Apple push key exists.
- App Store Connect API key exists.

No credential secret values were copied or printed.

## Local Mobile Configuration

Verified in the repository:

- iOS bundle identifier: `com.pulsesoc.app`
- Android package: `com.pulsesoc.app`
- EAS project id configured in `mobile/pulse-react-native/app.json`
- Firebase files are referenced by the active React Native app configuration.

## Runtime Provider Model

PulseSoc currently uses Expo Push Service as the primary native mobile push path:

- Native app requests Expo push tokens.
- Backend stores Expo tokens.
- Push worker sends Expo payloads.
- Expo tickets and receipts are tracked.

Direct APNs/FCM credentials are still useful for build/provider readiness and future fallback, but direct APNs/FCM sending is not the primary path validated by the runtime audits in this fix.

## Local Credential Audit Result

`scripts/push_credentials_readiness_audit.py --allow-missing-runtime-env` reported local APNs/FCM runtime credentials as unavailable. This is expected when production Railway variables are not mirrored into the local shell. It does not prove production is missing credentials.

## Production Verification Needed

Before declaring provider readiness complete, verify in Railway without exposing values:

- `APNS_BUNDLE_ID`
- `APNS_KEY_ID`
- `APNS_PRIVATE_KEY`
- `APNS_TEAM_ID`
- `FCM_PROJECT_ID`
- `FCM_CLIENT_EMAIL`
- `FCM_PRIVATE_KEY`
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `PUSH_DEFAULT_SOUND`
- `PUSH_BADGE_ENABLED`

The Command Center Worker should be restarted/redeployed after credential changes so readiness checks and provider clients refresh safely.
