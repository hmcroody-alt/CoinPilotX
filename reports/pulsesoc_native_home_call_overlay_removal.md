# PulseSoc Native Home Call Overlay Removal

Date: 2026-07-16

## Requirement

Home must never display the bottom active-call popup showing caller name, `Voice in progress`, or `End`, even when a legitimate voice or video call is active.

## Implementation

- Updated the existing `IncomingCallLayer` route policy.
- Added an explicit hidden route set for the floating active-call overlay:
  - `Home`
  - `Call`
- Added a navigation-state subscription so the call layer reacts to current route changes instead of relying on a stale route read.
- Preserved the active call polling, QA call fixture, accept/decline/end functions, and canonical `Call` route navigation.

## Home Behavior

- Home never mounts the active-call popup.
- Home does not reserve layout space for the active-call popup.
- Home does not render the popup invisibly.
- Home bottom navigation remains visible and tappable.
- No bottom padding is reserved for the removed popup.
- The overlay cannot intercept Home touches because it is not mounted on Home.

## Preserved Call Functionality

- Dedicated Call screen remains canonical for managing active calls.
- Active call state continues to refresh through `getActiveCalls()`.
- Incoming call handling remains unchanged.
- Ending a call remains available through the canonical Call UI and existing call APIs.
- Audio behavior is unchanged because no media/session logic was modified.

## QA Classification

Simulator verified:

- Native Home still opens with no call popup and no bottom spacing reserved.
- The bottom navigation remains visible and unobstructed in the inspected simulator state.

Code-path verified:

- Active call state still stores in `floatingCall`.
- Existing call open/end functions remain intact.
- Home suppression is a route policy, not a visual hide.
- QA active-call deep links seed `floatingCall`, but this simulator showed the iOS "Open in PulseSoc Native?" confirmation sheet and macOS Assistive Access is disabled, so the active-call Home state was verified by code path and audit rather than a clean no-prompt screenshot.

Screenshot evidence:

- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-home-call-overlay-removal/native-launch.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-home-call-overlay-removal/home-clean-no-call.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-home-call-overlay-removal/home-active-call-app-scheme.png`

Physical-device-only:

- Real microphone behavior.
- Real speaker/Bluetooth routing.
- Lock-screen push behavior.
- Background call audio.
- App-killed incoming call behavior.

## Remaining Work

- Physical-device release QA must verify audio/session behavior because Simulator cannot prove hardware routing.
