# Pulse App Store Connect Setup

Date: 2026-06-06

## Completed

- Accepted App Store Connect access gate was completed by the account owner.
- Registered Apple Bundle ID:
  - Description: `Pulse`
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
- The in-app product branding can remain `Pulse`.
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

## Still Required Before Public App Review

- Real-device iPhone QA.
- TestFlight internal testing pass.
- UGC report/block/account deletion verification.
- Premium payment compliance decision.
- Privacy nutrition labels completed by the account owner.
- Final support, privacy, and moderation links verified.
