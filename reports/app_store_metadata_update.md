# PulseSoc App Store Metadata Update

Date: 2026-07-04

## Objective

Correct public App Store metadata so the legal company name is consistently:

`CoinPlotXAI Inc.`

and no public metadata shows:

`CoinPilotXAI Inc.`

## App Store Connect Access

- App: PulseSoc
- App Store Connect app id: `6777591572`
- Current visible App Store Connect status: `iOS 1.0 Ready for Distribution`
- App Store Connect account header shown during audit: `ROODY CHERIE|2263496206...`
- URL inspected: `https://appstoreconnect.apple.com/apps/6777591572/distribution/ios/version/deliverable`

## Fields Checked

Visible App Store Connect metadata fields checked on the iOS 1.0 version page:

- Promotional Text
- Description
- Keywords
- Support URL
- Marketing URL
- Version
- Copyright
- App Store Privacy section link
- App Review section link
- Pricing and Availability link

Local metadata drafts checked:

- `mobile/pulse-react-native/store.config.json`
- `mobile/pulse-react-native/store-metadata/en-US/app-store.md`
- `mobile/pulse-react-native/store-metadata/en-US/app-review-notes-pulseshell.md`

Public App Store listing checked:

- `https://apps.apple.com/us/app/pulsesoc/id6777591572`

## Findings

The typo is still present in the public App Store description and in the App Store Connect Description field:

Before:

`PulseSoc is operated by CoinPilotXAI Inc.`

Expected after correction:

`PulseSoc is operated by CoinPlotXAI Inc.`

The visible Copyright field already shows the correct legal name:

`CoinPlotXAI Inc.`

The visible Support URL and Marketing URL are correct:

- Support URL: `https://pulsesoc.com/support`
- Marketing URL: `https://pulsesoc.com`

The local metadata drafts already use `CoinPlotXAI Inc.` correctly. The mismatch is between App Store Connect/public listing and the corrected local metadata draft.

## Update Attempt

I opened the PulseSoc iOS 1.0 App Store Connect metadata page and confirmed Chrome Find reported one `CoinPilotXAI` occurrence on the page.

I attempted to replace the selected typo in the Description field with `CoinPlotXAI`. The page did not accept the edit:

- The Description field behaved as read-only in the current `Ready for Distribution` version state.
- The visible Save button remained disabled after edit attempts.
- App Store Connect exposed explicit `Edit` links for fields such as Promotional Text and Copyright, but not for Description.
- I did not force state changes, manipulate private App Store Connect internals, or create a new version/build.

## Submission Status

Submission was not completed.

Reason:

The App Store Connect UI did not provide an editable Description control for the current iOS 1.0 `Ready for Distribution` version, and Save remained disabled. Because the requested typo is in the locked Description field, updating it likely requires an Apple-supported editable metadata state or a new app version metadata submission.

No bundle ID, SKU, App ID, pricing, availability, screenshots, subscriptions, app version, or build were changed.

## Apple Review Status

- Current version status remains: `Ready for Distribution`
- No new metadata submission was created.
- No Apple review status changed during this audit.

## Screenshots / Notes

Visual evidence was captured locally during the audit:

- `/tmp/appstore_distribution.png`
- `/tmp/appstore_find_native.png`
- `/tmp/appstore_verify_old_absent.png`
- `/tmp/appstore_direct_replace_attempt.png`
- `/tmp/appstore_word_replaced.png`

These were not committed because they are local audit artifacts and the requested commit scope is the report only.

## Exact Blocker

The Description field containing `CoinPilotXAI Inc.` is not editable from the current App Store Connect iOS 1.0 version page state.

## Required Next Steps

Use Apple's supported App Store Connect workflow:

1. Open PulseSoc in App Store Connect.
2. Confirm whether iOS 1.0 can be moved into an editable metadata state without changing the build.
3. If Apple requires a new version for Description changes, create the next iOS version metadata draft only after confirming the intended version number.
4. Update Description:
   - Replace `CoinPilotXAI Inc.` with `CoinPlotXAI Inc.`
5. Submit the metadata/version update for Apple review.
6. After Apple approval/propagation, verify the public App Store page no longer contains `CoinPilotXAI Inc.`

## Final Status

Blocked by App Store Connect metadata editability.

The typo is confirmed, the correct replacement is known, local metadata is already corrected, and no unsupported App Store Connect changes were attempted.
