# Pulse App Store and Play Store Readiness

Date: 2026-06-06

## Current Status

Pulse is now repo-ready for internal native build preparation, but not yet submission-ready for public App Store or Play Store release.

The repository now includes the native submission scaffolding that can be completed once Apple, Google, Firebase, APNs, and real-device QA access are available.

## Completed In Repo

- Expo app identity configured:
  - App name: Pulse
  - App Store listing name: PulseSoc
  - Scheme: `pulse`
  - iOS bundle ID: `com.pulsesoc.app`
  - Android package: `com.pulsesoc.app`
- Apple Bundle ID registered:
  - Description: `Pulse`
  - Bundle ID: `com.pulsesoc.app`
  - Associated Domains enabled
  - Push Notifications enabled
- App Store Connect app record created:
  - Apple app ID: `6777591572`
  - SKU: `pulse-ios-001`
  - Platform: iOS
- App Store metadata draft saved:
  - Subtitle: `Create, connect, stay aware`
  - Primary category: Social Networking
  - Secondary category: Photo & Video
  - iOS 1.0 public listing copy and URLs saved
  - Release mode set to manual
- App assets added:
  - `assets/icon.png`
  - `assets/adaptive-icon.png`
  - `assets/splash.png`
  - `assets/notification-icon.png`
- iOS usage descriptions added for camera, microphone, and photo library.
- iOS associated domains added for PulseSoc app links.
- Android app links added for PulseSoc.
- Android permissions added for camera, audio recording, media reads, and notifications.
- Expo notifications plugin configured with a notification icon and brand color.
- EAS build profiles added:
  - development
  - preview
  - production
- EAS submit configured with the real App Store Connect app ID.
- Google Play service account path protected with `.gitignore`.
- Store metadata drafts added:
  - App Store listing
  - Play Store listing
  - Data Safety / privacy labels draft
  - UGC moderation draft
  - Premium payment compliance draft

## Still Missing Before Public Submission

- Google Play Console app record.
- Firebase project and Android FCM credentials.
- APNs key/certificate or EAS-managed APNs credentials.
- Expo/EAS project ID.
- Google Play service account JSON stored locally but not committed.
- Test account credentials for Apple/Google review.
- Final screenshots for each required device class.
- App Privacy responses.
- Age Rating questionnaire.
- EU Digital Services Act trader status/compliance information.
- App Store reviewer credentials and contact details.
- Physical iPhone QA pass.
- Physical Android QA pass.
- First iOS build uploaded to TestFlight.
- TestFlight internal test pass.
- Google Play internal test pass.
- Native report/block entry points verified for user-generated content.
- Native account deletion/user data request path verified.
- Apple/Google Premium payment compliance decision.
- Controlled Expo SDK/security upgrade plan for current transitive dependency audit findings.

## Premium Risk

Pulse Premium and Founder Premium may be considered digital goods or digital subscriptions in mobile apps. Public production submission should not rely on Stripe checkout inside the native app until Apple and Google policy handling is finalized.

Recommended path:

- Show current Premium status in native.
- Avoid native Stripe checkout for public store builds until compliance is resolved.
- Prepare in-app purchase products if required.
- Sync Apple/Google receipts to the existing entitlement system.

## UGC Risk

Pulse includes user-generated posts, reels, videos, statuses, comments, messages, profiles, and marketplace/community surfaces.

Before public submission:

- Verify report content flow.
- Verify block/restrict user flow.
- Verify account deletion/data request flow.
- Verify admin moderation response path.
- Provide test account credentials to reviewers.

## Launch Gate

Pulse can move to internal build preparation after credentials are available.

Pulse should not be submitted for public store review until real-device QA and the remaining compliance gates above are complete.
