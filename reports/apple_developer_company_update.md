# Apple Developer Company Update Audit

Date: 2026-07-04

## Company Information

- Legal company: CoinPlotXAI Inc.
- D-U-N-S Number: 134170024
- Primary product: PulseSoc
- Website: https://pulsesoc.com
- Support email: support@pulsesoc.com
- App Store listing: https://apps.apple.com/us/app/pulsesoc/id6777591572

## Confirmed Current State

### Public App Store Listing

The public App Store listing is live and still shows individual-account identity in user-facing seller/developer fields:

- App name: PulseSoc
- App subtitle: Create, connect, stay aware
- Category: Social Networking
- Current public developer display: ROODY CHERIE226349620661
- Current public seller: ROODY CHERIE
- Current copyright: CoinPlotXAI Inc.
- Developer website: pulsesoc.com
- Privacy policy: pulsesoc.com
- App Store URL preserved: https://apps.apple.com/us/app/pulsesoc/id6777591572

Important issue found:

- The public app description still says `PulseSoc is operated by CoinPilotXAI Inc.` This should be corrected to `CoinPlotXAI Inc.` during the next editable metadata update.

### App Store Connect

Authenticated App Store Connect access was available in Chrome at:

- https://appstoreconnect.apple.com/apps

The Apps list showed:

- PulseSoc
- iOS 1.0
- Ready for Distribution

The app record opened at:

- https://appstoreconnect.apple.com/apps/6777591572/distribution

No bundle ID, App ID, subscriptions, users, analytics, ratings, reviews, or App Store URL changes were made.

### Apple Developer Membership Page

I attempted to open:

- https://developer.apple.com/account

The page repeatedly timed out under browser automation before account membership details could be read. Because changing membership/account data is sensitive and Apple may require Account Holder verification, no unsupported workaround was attempted.

## Account Type Assessment

Current account type is most likely still Individual.

Evidence:

- Public App Store seller is `ROODY CHERIE`.
- Public App Store developer display is `ROODY CHERIE226349620661`.
- Apple states that individual or sole proprietor accounts list the personal legal name as the App Store seller.
- Apple states that organization accounts list the legal entity name as the App Store seller.

Not fully confirmed:

- The Developer Program membership details page could not be read because the Apple Developer account page timed out in browser automation.

## D-U-N-S Status

Provided D-U-N-S Number:

- 134170024

Apple association status:

- Not confirmed inside Apple Developer membership details.

Operational interpretation:

- The D-U-N-S number is ready to be used in Apple's official Individual-to-Organization update request.
- If the D-U-N-S number is newly issued or updated, Apple may need time to receive refreshed D&B data before verification succeeds.

## Official Apple Requirements

Apple's official documentation says:

- Account Holder role is required to update Developer Program account information.
- Individual-to-Organization membership updates must be submitted as a request to Apple.
- The request requires organization details such as the D-U-N-S Number.
- Apple may request business documents to verify the organization.
- Companies must provide a D-U-N-S Number registered to their legal entity.
- DBAs, fictitious business names, trade names, and branches are not accepted for company/organization enrollment.
- For organization enrollment, the legal entity name appears as the App Store seller.

Relevant Apple documentation:

- https://developer.apple.com/help/account/membership/updating-your-account-information/
- https://developer.apple.com/help/account/membership/D-U-N-S/
- https://developer.apple.com/help/account/membership/program-enrollment/
- https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information/

## Required Manual Account Holder Actions

These actions must be performed by the Apple Developer Account Holder:

1. Sign in to https://developer.apple.com/account.
2. Open Membership Details.
3. Confirm the current membership type.
4. If the account is Individual, submit Apple's official request to update from Individual to Organization.
5. Use:
   - Legal entity name: CoinPlotXAI Inc.
   - D-U-N-S Number: 134170024
   - Website: https://pulsesoc.com
   - Support email: support@pulsesoc.com
6. Be prepared to provide business documents if Apple requests them.
7. Wait for Apple approval.
8. After Apple approval, verify the public App Store seller changes from `ROODY CHERIE` to `CoinPlotXAI Inc.`

## App Store Connect Metadata To Update After Account Alignment

When the app metadata is editable, update these user-facing fields:

- Description: replace `CoinPilotXAI Inc.` with `CoinPlotXAI Inc.`
- Support URL: ensure it points to a page with real support contact information.
- Marketing URL: ensure it points to https://pulsesoc.com or the intended PulseSoc product page.
- Copyright: already appears correct as `CoinPlotXAI Inc.`
- Privacy Policy URL: verify current URL is correct.
- User Privacy Choices URL: verify current URL is correct if provided.
- Support email/contact information: support@pulsesoc.com

Do not change:

- Bundle ID
- App ID
- Existing subscriptions
- Users
- Analytics
- Ratings
- Reviews
- App Store URL

## Fields Updated

No Apple account fields were changed.

Reason:

- The public App Store listing indicates the Apple account is still presenting as an individual seller.
- The Apple Developer membership page could not be reliably read under automation.
- Apple requires Account Holder action and may require verification/approval for Individual-to-Organization migration.

## Fields Awaiting Apple Approval

Likely pending once the Account Holder submits the official request:

- Developer Program legal entity
- Developer display/seller name
- D-U-N-S association
- Business/legal profile fields
- Agreements, tax, and banking records tied to the legal entity

## Manual Verification Checklist After Apple Approval

After Apple confirms migration:

- Public App Store seller is `CoinPlotXAI Inc.`
- Public developer name is `CoinPlotXAI Inc.`
- App description references `CoinPlotXAI Inc.`
- Copyright remains `CoinPlotXAI Inc.`
- Support URL works and shows support contact details.
- Marketing URL works.
- Privacy policy URL works.
- Agreements are accepted under the organization.
- Tax and banking records are complete for the organization.
- App Store URL remains unchanged.
- PulseSoc app identity and user data remain preserved.

## Current Blocker

Apple Developer account migration requires Account Holder action and Apple review. I did not attempt unsupported changes or submit account updates without the Account Holder actively completing Apple's official request flow.
