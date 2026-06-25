# PulseSoc App Store Metadata Draft

## App Name

PulseSoc

## Subtitle

Create, connect, stay aware

## Promotional Text

PulseSoc brings social posting, reels, videos, messaging, notifications, profiles, and community tools into one mobile-first social space.

## Description

PulseSoc is a social platform for creating, learning, and connecting through posts, videos, reels, messages, notifications, profiles, and community tools.

With PulseSoc, you can follow creators, explore video content, check notifications, manage your profile, and stay connected through direct messages. PulseSoc is operated by CoinPilotXAI Inc. and is built around user control, moderation, privacy-aware design, and clear communication.

PulseSoc is not financial, investment, legal, or betting advice. Paid digital access is not available in this iOS build.

## Keywords

social, creator, reels, video, messaging, notifications, community, PulseSoc

## Support URL

https://pulsesoc.com/support

## Marketing URL

https://pulsesoc.com

## Privacy Policy URL

https://pulsesoc.com/privacy

## Copyright

CoinPilotXAI Inc.

## Review Notes

This update addresses App Review Guideline 4, Guideline 5.1.1(v), Guideline 3.1.1, Guideline 2.1(a), and Guideline 1.2 feedback from PulseSoc iOS version 1.0 review.

PulseSoc includes user-generated posts, media, comments, messages, notifications, profiles, statuses, marketplace/community surfaces, and live/video surfaces.

User-generated content safety:
- Users must agree to the PulseSoc Terms, Privacy Policy, and no-tolerance rules for objectionable content and abusive users before registering or logging in.
- The Terms state that PulseSoc has no tolerance for objectionable content or abusive users.
- Post menus include Report and Block actions. Report sends the content to moderation. Block creates a block record, creates an open moderation report, and removes that creator's visible posts from the user's feed.
- PulseSoc moderation acts on objectionable-content reports within 24 hours by removing violating content and restricting, suspending, or ejecting offending users when required.

Premium and payments:
- The iOS native build does not present Stripe checkout or external billing for paid digital content.
- Premium purchase surfaces are disabled in native iOS context.
- Premium purchase, Founder activation, billing portal, creator checkout, marketplace checkout, course checkout, Premium Intelligence, Premium Portfolio, UNDX Premium, and premium-only appearance APIs are disabled in native iOS context until Apple in-app purchase products are implemented and approved.
- Existing web subscriptions do not unlock paid digital premium surfaces inside this iOS build.
- Account status, entitlement, purchase, order, billing confirmation, creator monetization, creator AI, paid course, and premium dashboard APIs return iOS core-only responses in the submitted iOS context.
- The native Premium screen does not open Stripe, checkout, billing portal, Premium Intelligence, creator monetization, paid courses, or external-purchase subscription benefits on iOS.

Device support:
- The next review build is iPhone-only. iPad support is disabled until a dedicated iPad layout passes QA for all supported iPad screen sizes.

Account deletion:
- Users can initiate permanent account deletion inside the app from PulseSoc Settings > Account > Delete my account.
- The deletion screen requires password confirmation and explicit acknowledgement that deletion is permanent.
- The direct authenticated deletion path is `/account/delete`.

Responsive/tappable controls:
- The submitted build uses the native WebView user agent `PulseSocNativeApp/1.0` so server-side App Store compliance gates apply consistently.
- The auth/account screens include an iPad-width breakpoint to prevent left-cropped layouts if Apple tests on iPad hardware, while the app configuration remains iPhone-only.

App Review Information required before resubmission:
- Add valid demo login credentials in App Store Connect.
- Attach a physical-device screen recording showing the Terms/EULA before signup/login, the Report action, the Block action, and the account deletion path from Settings > Account > Delete my account.
