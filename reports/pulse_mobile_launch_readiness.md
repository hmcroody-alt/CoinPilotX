# Pulse Mobile Launch Readiness

Date: 2026-06-06

## Recommendation

Continue stabilization before store launch. The next mobile milestone should be real-device QA for auth, push notification delivery, media playback, deep links, and session persistence.

## Firebase Requirements

- Firebase project for Android push notifications.
- FCM server credentials stored only in production secrets.
- Android package name reserved as `com.pulsesoc.app`.
- Notification click payload contract aligned with Pulse deep links.

## APNs Requirements

- Apple Developer account.
- APNs key or certificate configured through Expo/EAS or direct native provider.
- iOS bundle identifier reserved as `com.pulsesoc.app`.
- Push entitlement and notification permission copy reviewed.

## Android Requirements

- Play Console account.
- App signing setup.
- Privacy policy URL on `https://pulsesoc.com/privacy`.
- Data safety answers for account data, user content, notifications, diagnostics, payments, and optional contacts/media.

## App Store Requirements

- Apple Developer Program membership.
- App privacy nutrition labels.
- Support URL and marketing URL.
- Account deletion path documented.
- Moderation/reporting controls verified for user-generated content.

## Play Store Requirements

- Play Console access.
- Data Safety form.
- Content rating.
- UGC policy compliance.
- Test track before production rollout.

## Privacy Disclosures

Pulse must disclose account data, profile content, posts, media uploads, messaging metadata/content, notification tokens, payment status, security logs, and diagnostics. Secrets, stream keys, and private provider tokens must never be exposed in app builds.

## Push Architecture

- Browser/PWA push continues through existing web push storage.
- Native foundation starts with Expo push tokens.
- Future direct APNs/FCM migration should preserve the `pulse_notification_devices` table and provider metadata.
- Notification clicks should route through `pulse://` deep links with web fallbacks.

## Launch Gate

Do not generate store production builds until real-device push delivery, auth persistence, deep links, and media playback are verified end to end.
