# PulseSoc App Store Connect Setup

Date: 2026-06-06

## Completed

- Accepted App Store Connect access gate was completed by the account owner.
- Registered Apple Bundle ID:
  - Description: `PulseSoc`
  - Bundle ID: `com.pulsesoc.app`
  - Team ID prefix: `87ZC69AGSR`
  - Associated Domains: enabled
  - Push Notifications: enabled
- Created App Store Connect app record:
  - App Store listing name: `PulseSoc`
  - Apple app ID: `6777591572`
  - Platform: iOS
  - Primary language: English (U.S.)
  - SKU: `pulse-ios-001`
  - Bundle ID: `com.pulsesoc.app`
- Saved App Information:
  - Subtitle: `Create, connect, stay aware`
  - Primary category: Social Networking
  - Secondary category: Photo & Video
- Saved iOS 1.0 version metadata:
  - Promotional text
  - Description
  - Keywords
  - Support URL: `https://pulsesoc.com/support`
  - Marketing URL: `https://pulsesoc.com`
  - Copyright: `CoinPilotXAI Inc.`
  - Release mode: manual release
- Updated EAS submit configuration with the real App Store Connect app ID.

## Notes

- The plain App Store listing name `Pulse` was unavailable in App Store Connect, so the store listing was created as `PulseSoc`.
- The in-app product branding and mobile app package now use `PulseSoc`.
- No certificates, private keys, banking, tax, payment, or production secrets were created or changed.
- No app was submitted for review.
- Reviewer credentials were not entered.
- App Privacy, Age Ratings, EU Digital Services Act, screenshots, and build upload remain open.

## Still Required Before TestFlight

- Create or link Expo/EAS project ID.
- Configure iOS signing through EAS or Apple certificates.
- Configure APNs credentials for native push.
- Generate a first internal iOS build.
- Upload the build to TestFlight.
- Add reviewer/test account details after a working build exists.
- Complete App Privacy responses.
- Complete EU Digital Services Act trader status if distribution includes the EU.
- Add screenshots and final metadata.

## 2026-06-07 Continuation

- Verified the native app package is branded as `PulseSoc`.
- Verified the app package uses the PulseSoc logo for app icon, adaptive icon, splash, and notification icon.
- Aligned the alternate top-level Expo shell at `mobile/app.json` to `PulseSoc`, `com.pulsesoc.app`, and the PulseSoc logo assets.
- App Store Connect browser automation reached the Apple login page; the account owner must complete login and any 2FA manually before more dashboard work can continue.
- EAS CLI is available through `npx eas-cli`, but the local Expo account is not logged in yet.
- No App Store review submission was attempted.

## Still Required Before Public App Review

- Real-device iPhone QA.
- TestFlight internal testing pass.
- UGC report/block/account deletion verification.
- Premium payment compliance decision.
- Privacy nutrition labels completed by the account owner.
- Final support, privacy, and moderation links verified.
