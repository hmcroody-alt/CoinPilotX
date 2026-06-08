# PulseSoc Mobile Real Device Parity QA Report

Status: in progress, defects found in iOS build 12.

This report tracks the real-device proof step for PulseSoc mobile web parity. No new feature phase has started.

## Builds Under QA

### iOS

- Platform: iOS
- EAS build ID: `61fe9d0f-5aba-4f89-bfd4-53231b1483aa`
- Build number: `12`
- Status: installed and screenshot-tested by user; parity defects found
- IPA artifact: `https://expo.dev/artifacts/eas/d6R49rE5bLbmnKaHvWKsFU.ipa`
- App Store Connect upload: completed through EAS Submit
- Submission ID: `94bb3e74-8f92-42dd-b552-c8b7a4027da9`
- Next step: wait for Apple processing, then install build 12 from TestFlight on the iPhone.

Build 12 must be replaced by a corrected build because real-device screenshots showed safe-area and session-state defects.

### Android

- Platform: Android
- EAS build ID: `4e8bed11-1228-4bf6-9346-41543e02aa02`
- Version code: `8`
- Status: finished
- AAB artifact: `https://expo.dev/artifacts/eas/aGykrL2Ha521PC4Wjg3YJw.aab`
- CLI Play submission status: blocked because `mobile/pulse-react-native/credentials/google-play-service-account.json` is not present locally.
- Next step: upload the AAB to the Play Console internal testing track, or provide a safe Play service account key through the approved local credential path.

## Web Reference State

- Current in-app browser URL: `https://pulsesoc.com/login?next=/pulse`
- Current browser state: logged out
- Authenticated web parity screenshots cannot be captured until the user signs in to PulseSoc in the browser.

Existing web reference screenshots available in the repo:

- `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_feed_mobile_390.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_reels_mobile_390.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_status_mobile_390.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_feed_desktop_1440.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/pulse_reels_desktop_1440.png`

## Required Real Device Screenshot Matrix

| Screen | iPhone build 12 screenshot | Android build 8 screenshot | Web comparison | Status |
|---|---:|---:|---:|---|
| Feed | Received | Pending | Pending authenticated browser login | Defects found |
| Reels | Pending | Pending | Pending authenticated browser login | Blocked |
| Videos | Received | Pending | Pending authenticated browser login | Defects found |
| Messages | Received | Pending | Pending authenticated browser login | Defects found |
| Notifications | Received | Pending | Pending authenticated browser login | Defects found |
| Profile | Pending | Pending | Pending authenticated browser login | Blocked |
| Premium | Received | Pending | Pending authenticated browser login | Defects found |

## Verification Checklist

The following must be checked on real iPhone and Android hardware before parity can be declared complete:

- No placeholder icons.
- No diagnostic text.
- No raw timestamps.
- No fake/demo UI.
- Logged-in state works everywhere.
- Videos render properly.
- Reels are immersive.
- Feed looks like a social platform.
- Messages look like a messaging product.
- Premium looks like a subscription product.
- Safe area handling is correct.
- Bottom navigation spacing is correct.
- Text does not truncate awkwardly.
- No horizontal overflow.
- Dark theme is consistent.
- Tablet responsiveness is acceptable.

## Side-By-Side Comparison Plan

For each screen:

1. Capture authenticated web reference screenshot.
2. Capture iPhone build 12 screenshot.
3. Capture Android build 8 screenshot.
4. Place screenshots side by side in this report.
5. Score parity from 0 to 10.
6. Log defects and fix any screen that still looks like a placeholder before declaring success.

## Current Parity Scores

Scores are intentionally not assigned yet because real-device screenshots have not been captured.

| Screen | Current score | Reason |
|---|---:|---|
| Feed | Not scored | Needs iPhone/Android screenshots |
| Reels | Not scored | Needs iPhone/Android screenshots |
| Videos | Not scored | Needs iPhone/Android screenshots |
| Messages | Not scored | Needs iPhone/Android screenshots |
| Notifications | Not scored | Needs iPhone/Android screenshots |
| Profile | Not scored | Needs iPhone/Android screenshots |
| Premium | Not scored | Needs iPhone/Android screenshots |

## Defects Found So Far

- Android install path is not complete from CLI because the Play service account key is not available locally.
- Authenticated web comparison is blocked because the in-app browser is logged out.
- iOS build 12 Feed: top chrome and hero content overlap the iPhone Dynamic Island/status area.
- iOS build 12 Messages: header is clipped, and the empty conversations card bleeds sideways into the top area.
- iOS build 12 Videos: protected endpoint returned `Login required`, leaving a red API error on a product surface.
- iOS build 12 Notifications: protected endpoint returned `Login required`, leaving a red API error on a product surface.
- iOS build 12 Premium: protected endpoint returned `Login required`, while still rendering stale plan fallback data.
- iOS build 12 bottom navigation: iPhone home indicator overlaps the tab labels/spacing.

## Fixes Applied Before This QA Phase

- Replaced the mobile demo shell with the production PulseSoc navigator.
- Added web-style PulseSoc chrome, colors, hero cards, pills, and bottom navigation labels.
- Feed now uses real `/api/pulse/feed`, `/api/pulse/status/rail`, and `/api/pulse/live-now`.
- Reels now render as full-screen immersive media with muted autoplay and web-style overlay.
- Videos now use large reserved media surfaces with contain fitting and Mux playback support.
- Messages use Communications V2 instead of diagnostics.
- Notifications use real notification APIs and user-facing cards.
- Profile and Premium bind to real account APIs.

## Fixes Applied After iOS Build 12 Screenshots

- Added explicit top safe-area support to `PulseTopBar` and enabled it on Feed, Videos, Messages, and Notifications.
- Moved the Reels overlay below the iPhone safe area.
- Increased bottom tab height and bottom padding so the iPhone home indicator no longer overlaps tab labels.
- Rebuilt Messages header layout from a horizontal header/list hybrid into a contained vertical header with a separate conversation strip.
- Changed empty Messages conversation state to full-width contained card instead of a sideways clipped panel.
- Protected Videos, Messages, Notifications, and Premium now force a clean login transition on stale/expired session errors instead of showing raw `Login required` product errors.

## Remaining Work Before Declaring Success

- Install iOS build 12 on a real iPhone through TestFlight after Apple processing.
- Build and install corrected iOS build 13 or later after the safe-area/session fixes.
- Upload Android build 8 AAB to internal testing or provide the Play service account key for CLI submission.
- Install Android build 8 on a real Android device.
- Sign in to PulseSoc in the in-app browser so authenticated web references can be captured.
- Capture all required screenshots.
- Build side-by-side comparisons and assign parity scores.
- Fix any remaining placeholder-looking screen before final approval.
