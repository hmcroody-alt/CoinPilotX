# Pulse Mobile Launch Readiness

Date: 2026-06-06

## Recommendation

Continue stabilization before store launch. The next mobile milestone should be real-device QA for auth, push notification delivery, media playback, deep links, and session persistence.

The native foundation exists at `mobile/pulse-react-native` and typechecks locally. It should move through TestFlight and Google Play internal testing only after real-device auth, notification, media, and Premium flows pass.

## Firebase Requirements

- Firebase project for Android push notifications.
- FCM server credentials stored only in production secrets.
- Android package name reserved as `com.pulsesoc.app`.
- Notification click payload contract aligned with Pulse deep links.
- Expo project ID configured in `app.json` or `EXPO_PUBLIC_EAS_PROJECT_ID` before push QA.

## APNs Requirements

- Apple Developer account.
- APNs key or certificate configured through Expo/EAS or direct native provider.
- iOS bundle identifier reserved as `com.pulsesoc.app`.
- Push entitlement and notification permission copy reviewed.
- Associated domains configured for `pulsesoc.com` before universal-link release.

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

## Native Foundation Status

- Expo app exists at `mobile/pulse-react-native`.
- Active entrypoint: `App.tsx` -> `src/App.tsx`.
- Auth: login, register, password recovery, logout, session restore.
- Navigation: Feed, Reels, Videos, Messages, Notifications, Profile, Premium.
- Push: Expo token registration posts to `/api/push/subscribe`.
- Deep links: `pulse://`, PulseSoc.com, and CoinPilotX fallback domains.
- API reuse: native screens call existing Pulse JSON endpoints.
- Store builds: intentionally not generated yet.

## Privacy Disclosures

Pulse must disclose account data, profile content, posts, media uploads, messaging metadata/content, notification tokens, payment status, security logs, and diagnostics. Secrets, stream keys, and private provider tokens must never be exposed in app builds.

## Push Architecture

- Browser/PWA push continues through existing web push storage.
- Native foundation starts with Expo push tokens.
- Future direct APNs/FCM migration should preserve the `pulse_notification_devices` table and provider metadata.
- Notification clicks should route through `pulse://` deep links with web fallbacks.

## Launch Gate

Do not generate store production builds until real-device push delivery, auth persistence, deep links, and media playback are verified end to end.

## Immediate Next QA

- Install on a physical iPhone with Expo Go or development build.
- Install on a physical Android device with Expo Go or development build.
- Verify login, logout, session restore, and password recovery.
- Verify Feed, Reels, Videos, Messages, Notifications, Profile, and Premium load from production APIs.
- Verify push permission, token registration, and notification click routing.
- Verify video/reel playback behavior before expanding native media controls.
